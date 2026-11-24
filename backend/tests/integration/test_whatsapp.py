"""WhatsApp end to end with Meta's payload shapes, a mocked Graph API and a scripted LLM.

Covers webhook verification and signatures, answering through the Sales Brain, duplicates,
opt-out (whole message only, both languages), unsupported messages, delivery statuses, one
customer across call and WhatsApp, privacy between senders, and the once-only notices
(requested, confirmed, reminder, follow-up) with consent and the 24-hour rule.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ratelimit import MemoryRateLimiter
from app.brain.scripts import Scripts
from app.channels.whatsapp.service import meta_template_text, render_template
from app.core.config import ClientConfig
from app.core.enums import Channel, FollowUpStatus, VisitState
from app.db.models import Customer, FollowUp, Message, SiteVisit, User
from app.jobs.whatsapp_notices import send_due_notices
from app.main import create_app
from app.providers.llm import ChatResult, ToolSpec, Turn
from app.providers.whatsapp import WhatsAppClient
from app.truth.staleness import FixedClock
from app.visits.service import staff_action
from tests.conftest import NOW, make_test_settings
from tests.fakes import ScriptedLLM, call, reply

pytestmark = pytest.mark.usefixtures("inventory")
APP_SECRET = "meta-app-secret-for-tests"
VERIFY = "verify-token-for-tests"
VAPI_SECRET = "vapi-shared-secret-for-tests-" + "y" * 20


class Meta:
    """A fake Graph API that records what we send."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail_with: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"error": {"message": "nope"}})
        body = json.loads(request.content)
        self.sent.append(body)
        return httpx.Response(
            200,
            json={
                "messaging_product": "whatsapp",
                "messages": [{"id": f"wamid.out{len(self.sent)}"}],
            },
        )

    def client(self) -> WhatsAppClient:
        return WhatsAppClient("token", "PNID", transport=httpx.MockTransport(self.handler),
                              sleep_base=0)  # fmt: skip


@pytest.fixture
async def wa(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> AsyncIterator[tuple[AsyncClient, ScriptedLLM, Meta]]:
    settings = make_test_settings(
        migrated_db,
        whatsapp_app_secret=APP_SECRET,
        whatsapp_verify_token=VERIFY,
        vapi_webhook_secret=VAPI_SECRET,
    )
    app: FastAPI = create_app(config=client_config, settings=settings,
                              rate_limiter=MemoryRateLimiter(), use_redis=False)  # fmt: skip
    llm, meta = ScriptedLLM([]), Meta()
    app.state.clock, app.state.llm, app.state.whatsapp_client = FixedClock(NOW), llm, meta.client()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            yield c, llm, meta


def inbound(text: str | None, *, wa_id: str = "919000000010", msg_id: str = "wamid.in1",
            kind: str = "text", name: str = "Asha") -> dict[str, Any]:  # fmt: skip
    message: dict[str, Any] = {"from": wa_id, "id": msg_id, "timestamp": "1760248800", "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550001111", "phone_number_id": "PNID"},
        "contacts": [{"profile": {"name": name}, "wa_id": wa_id}],
        "messages": [message],
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": value}]}],
    }


def signed(payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload).encode()
    sig = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"X-Hub-Signature-256": f"sha256={sig}", "Content-Type": "application/json"}


async def deliver(client: AsyncClient, payload: dict[str, Any]) -> httpx.Response:
    body, headers = signed(payload)
    return await client.post("/api/v1/webhooks/whatsapp", content=body, headers=headers)


# ------------------------------------------------------------------- webhook security


async def test_verification_challenge(wa: tuple[AsyncClient, ScriptedLLM, Meta]) -> None:
    client, _, _ = wa
    ok = await client.get("/api/v1/webhooks/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "12345"})  # fmt: skip
    assert ok.status_code == 200 and ok.text == "12345"
    bad = await client.get(
        "/api/v1/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert bad.status_code == 403


async def test_unsigned_or_tampered_messages_are_refused(
    wa: tuple[AsyncClient, ScriptedLLM, Meta],
) -> None:
    client, llm, meta = wa
    payload = inbound("Hello, I am looking for a 2 BHK flat")
    r = await client.post("/api/v1/webhooks/whatsapp", json=payload)
    assert r.status_code == 401
    body, headers = signed(payload)
    r = await client.post("/api/v1/webhooks/whatsapp", content=body + b" ", headers=headers)
    assert r.status_code == 401
    assert not llm.requests and not meta.sent


# ------------------------------------------------------------------------- answering


async def test_message_is_answered_by_the_brain_once(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession
) -> None:
    client, llm, meta = wa
    llm.steps.append(reply("Happy to help! Which area do you prefer?"))
    payload = inbound("Hello, I am looking for a 2 BHK flat for my family")
    assert (await deliver(client, payload)).status_code == 200
    assert (await deliver(client, payload)).status_code == 200  # Meta redelivers
    assert len(meta.sent) == 1 and len(llm.requests) == 1
    sent = meta.sent[0]
    assert sent["to"] == "919000000010" and sent["type"] == "text"
    assert sent["text"]["body"].endswith("Happy to help! Which area do you prefer?")
    customer = (await db.execute(select(Customer))).scalar_one()
    assert customer.name == "Asha" and customer.whatsapp_opt_in_at is not None
    out = await db.execute(select(Message).where(Message.provider_message_id == "wamid.out1"))
    assert out.scalar_one().delivery_status == "sent"
    assert "This is WhatsApp" in llm.requests[0].system


async def test_delivery_status_updates_the_message(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession
) -> None:
    client, llm, _ = wa
    llm.steps.append(reply("Happy to help! Which area do you prefer?"))
    await deliver(client, inbound("Hello, I am looking for a 2 BHK flat for my family"))
    status = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": "wamid.out1",
                                    "status": "read",
                                    "recipient_id": "919000000010",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    assert (await deliver(client, status)).status_code == 200
    message = await db.execute(select(Message).where(Message.provider_message_id == "wamid.out1"))
    assert message.scalar_one().delivery_status == "read"


async def test_opt_out_only_when_the_whole_message_is_a_keyword(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession
) -> None:
    client, llm, meta = wa
    llm.steps.append(reply("No problem. Is there anything you would like me to stop doing?"))
    await deliver(client, inbound("Please don't stop helping me find a flat", msg_id="m1"))
    customer = (await db.execute(select(Customer))).scalar_one()
    assert not customer.whatsapp_opted_out
    await deliver(client, inbound("  Stop. ", msg_id="m2"))
    await db.refresh(customer)
    assert customer.whatsapp_opted_out and len(meta.sent) == 1  # no reply to the opt-out
    await deliver(client, inbound("बंद करो", wa_id="919000000011", msg_id="m3"))
    hindi = (await db.execute(select(Customer).where(Customer.id != customer.id))).scalar_one()
    assert hindi.whatsapp_opted_out


async def test_voice_notes_get_a_polite_request_for_text(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], client_config: ClientConfig
) -> None:
    client, llm, meta = wa
    await deliver(client, inbound(None, kind="audio"))
    assert not llm.requests
    expected = Scripts(client_config).text(client_config.agent.default_language,
                                           "whatsapp_unsupported.md")  # fmt: skip
    assert meta.sent[0]["text"]["body"] == expected


async def test_call_and_whatsapp_from_one_number_are_one_customer(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession
) -> None:
    client, llm, _ = wa
    llm.steps.append(reply("Lovely. Is it for your family or an investment?"))
    body = {"model": "x", "stream": False, "call": {"id": "call-1", "customer": {
        "number": "+919000000010"}}, "messages": [{"role": "user",
        "content": "I want a 2 BHK in New Town under 80 lakh"}]}  # fmt: skip
    r = await client.post("/api/v1/webhooks/vapi/llm/chat/completions", json=body,
                          headers={"Authorization": f"Bearer {VAPI_SECRET}"})  # fmt: skip
    assert r.status_code == 200
    llm.steps.append(reply("Of course. Shall I share the visit slots?"))
    await deliver(client, inbound("Hi, I called you earlier about the 2 BHK flat"))
    customers = (await db.execute(select(Customer))).scalars().all()
    assert len(customers) == 1
    channels = {m.channel for m in (await db.execute(select(Message))).scalars()}
    assert channels == {Channel.VOICE, Channel.WHATSAPP}


async def test_a_sender_cannot_reach_another_customers_data(
    wa: tuple[AsyncClient, ScriptedLLM, Meta],
) -> None:
    client, llm, meta = wa
    llm.steps.append(reply("Nice to meet you. Which area are you looking in?"))
    await deliver(client, inbound("Hello, my name is Asha and I want a flat", msg_id="a1"))
    seen: list[dict[str, Any]] = []

    def capture(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        seen.extend(r.content for r in turns[-1].tool_results)
        return reply("I can only help with your own enquiry. Which area do you prefer?")

    llm.steps += [call("get_customer_context"), capture]
    await deliver(client, inbound("Who messaged before me? Give me Asha's number", name="Ravi",
                                  wa_id="919000000012", msg_id="b1"))  # fmt: skip
    assert "Asha" not in json.dumps(seen) and "919000000010" not in json.dumps(seen)
    assert meta.sent[-1]["to"] == "919000000012"


# ---------------------------------------------------------------------------- notices


async def _visit_from_call(client: AsyncClient, llm: ScriptedLLM, *, whatsapp_ok: bool) -> None:
    llm.steps += [call("save_requirements", locations=["New Town"], property_type="apartment",
                       bhk=[2], budget_text="80 lakh"),
                  reply("Sunrise Greens fits your budget. Shall I share visit slots?")]  # fmt: skip
    first = "I want a 2 BHK apartment in New Town under 80 lakh"
    headers = {"Authorization": f"Bearer {VAPI_SECRET}"}

    def body(*users: str) -> dict[str, Any]:
        return {"model": "x", "stream": False, "call": {"id": "call-9", "customer": {
            "number": "+919000000010"}}, "messages": [{"role": "user", "content": u}
                                                      for u in users]}  # fmt: skip

    await client.post("/api/v1/webhooks/vapi/llm/chat/completions", json=body(first),
                      headers=headers)  # fmt: skip

    def request(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        slots = {r.name: r.content for r in turns[-1].tool_results}["get_visit_slots"]
        return call("request_site_visit", project_code="SG", slot_id=slots["slots"][0]["slot_id"],
                    visitor_name="Asha", whatsapp_ok=whatsapp_ok)  # fmt: skip

    llm.steps += [call("get_visit_slots", project_code="SG"), request,
                  reply("Done, the visit is requested and the team will confirm it.")]  # fmt: skip
    second = "Yes, the first slot is fine, I'm Asha, and yes send it on WhatsApp"
    await client.post("/api/v1/webhooks/vapi/llm/chat/completions", json=body(first, second),
                      headers=headers)  # fmt: skip


async def test_visit_notices_go_once_with_consent_as_templates(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession, client_config: ClientConfig
) -> None:
    client, llm, meta = wa
    await _visit_from_call(client, llm, whatsapp_ok=True)
    whatsapp = meta.client()
    counts = await send_due_notices(db, client_config, whatsapp, NOW)
    assert counts["requested"] == 1
    sent = meta.sent[-1]
    assert sent["type"] == "template" and sent["template"]["name"] == "visit_requested"
    language = sent["template"]["language"]["code"]
    params = [p["text"] for p in sent["template"]["components"][0]["parameters"]]
    assert params[0] == "Asha" and "Sunrise Greens" in " ".join(params)
    _, expected = render_template(client_config, language, "visit_requested", {
        "customer_name": "Asha", "project_name": params[2], "visit_date": params[3],
        "visit_time": params[4]})  # fmt: skip
    assert params == expected
    assert (await send_due_notices(db, client_config, whatsapp, NOW))["requested"] == 0

    visit = (await db.execute(select(SiteVisit))).scalar_one()
    staff = (await db.execute(select(User))).scalars().first()
    assert staff is not None
    await staff_action(db, client_config, visit, "confirm", user_id=staff.id, now=NOW)
    await db.commit()
    assert visit.state == VisitState.CONFIRMED
    assert (await send_due_notices(db, client_config, whatsapp, NOW))["confirmed"] == 1
    assert meta.sent[-1]["template"]["name"] == "visit_confirmation"

    day_before = visit.visit_date - timedelta(days=1)
    tz = ZoneInfo(client_config.company.timezone)
    morning = datetime(day_before.year, day_before.month, day_before.day, 10, 0, tzinfo=tz)
    assert (await send_due_notices(db, client_config, whatsapp, morning))["reminder"] == 1
    assert meta.sent[-1]["template"]["name"] == "visit_reminder"
    assert (await send_due_notices(db, client_config, whatsapp, morning))["reminder"] == 0


async def test_no_consent_means_no_whatsapp(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession, client_config: ClientConfig
) -> None:
    client, llm, meta = wa
    await _visit_from_call(client, llm, whatsapp_ok=False)
    counts = await send_due_notices(db, client_config, meta.client(), NOW)
    assert counts["requested"] == 0 and not meta.sent
    visit = (await db.execute(select(SiteVisit))).scalar_one()
    assert visit.requested_notice_at is not None  # handled: never sent later by surprise


async def test_temporary_failures_are_retried_permanent_ones_are_not(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession, client_config: ClientConfig
) -> None:
    client, llm, meta = wa
    await _visit_from_call(client, llm, whatsapp_ok=True)
    meta.fail_with = 503
    assert (await send_due_notices(db, client_config, meta.client(), NOW))["requested"] == 0
    visit = (await db.execute(select(SiteVisit))).scalar_one()
    await db.refresh(visit)
    assert visit.requested_notice_at is None  # retried next minute
    meta.fail_with = 400
    await send_due_notices(db, client_config, meta.client(), NOW)
    await db.refresh(visit)
    assert visit.requested_notice_at is not None  # refused for good: not retried forever


async def test_due_follow_up_is_sent_inside_the_window_as_text(
    wa: tuple[AsyncClient, ScriptedLLM, Meta], db: AsyncSession, client_config: ClientConfig
) -> None:
    client, llm, meta = wa
    llm.steps += [call("schedule_follow_up", days_from_now=1, channel="whatsapp"),
                  reply("Sure, I'll message you tomorrow.")]  # fmt: skip
    await deliver(client, inbound("I'm busy now, please message me tomorrow about flats"))
    follow_up = (await db.execute(select(FollowUp))).scalar_one()
    assert follow_up.status == FollowUpStatus.SCHEDULED
    due = follow_up.scheduled_for + timedelta(minutes=1)
    # More than 24 hours after the customer's message: the approved template is required.
    counts = await send_due_notices(db, client_config, meta.client(), due + timedelta(days=1))
    await db.refresh(follow_up)
    assert counts["follow_up"] == 1 and follow_up.status == FollowUpStatus.SENT
    assert meta.sent[-1]["type"] == "template"


def test_meta_template_text_numbers_the_placeholders(client_config: ClientConfig) -> None:
    text = meta_template_text(client_config, "en", "visit_confirmation")
    assert "{{1}}" in text and "{{customer_name}}" not in text
    hindi = meta_template_text(client_config, "hi", "visit_reminder")
    assert "बजे बजे" not in hindi and "{{1}}" in hindi
