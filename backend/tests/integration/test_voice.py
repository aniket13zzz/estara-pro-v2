"""Voice channel (Vapi) end to end with replayed payloads and a scripted LLM (no live calls).

Covers the shared-secret check, the custom-LLM turn (plain and streamed), repeated turns,
human transfer and the safe fallbacks, assistant-request, the end-of-call report (stored once),
the assistant definition and sync, the dashboard pages' API and staff visit actions, and a
whole demo call replayed from start to the staff confirming the visit.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ratelimit import MemoryRateLimiter
from app.brain.scripts import Scripts
from app.channels.vapi import sync as vapi_sync
from app.channels.vapi.assistant import VoiceNotReady, build_assistant, first_message
from app.core.config import ClientConfig
from app.core.enums import LeadState, VisitState
from app.core.settings import Settings
from app.db.models import (
    AgentState,
    AuditLog,
    Call,
    Customer,
    Lead,
    Message,
    SiteVisit,
    WebhookEvent,
)
from app.main import create_app
from app.providers.llm import ChatResult, ToolSpec, Turn
from app.truth.staleness import FixedClock
from tests.conftest import NOW, make_test_settings
from tests.fakes import ScriptedLLM, call, reply
from tests.helpers import ADMIN, STAFF, login

pytestmark = pytest.mark.usefixtures("inventory")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "vapi"
SECRET = "vapi-shared-secret-for-tests-" + "y" * 20
AUTH = {"Authorization": f"Bearer {SECRET}"}


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def custom(config: ClientConfig) -> ClientConfig:
    """The custom LLM mode these tests cover, with the voice fields still empty."""
    voice = config.voice.model_copy(update={"mode": "custom_llm"})
    return config.model_copy(update={"voice": voice})


def ready(config: ClientConfig) -> ClientConfig:
    voice = config.voice.model_copy(update={
        "mode": "custom_llm",
        "tts": config.voice.tts.model_copy(update={"model": "sonic-3", "voice_id": "voice-123"}),
        "stt": config.voice.stt.model_copy(update={"provider": "deepgram", "model": "nova-3"}),
    })  # fmt: skip
    return config.model_copy(update={"voice": voice})


def voice_settings(migrated_db: str, **extra: object) -> Settings:
    values: dict[str, object] = {
        "vapi_webhook_secret": SECRET,
        "public_base_url": "https://estara.example.in",
        **extra,
    }
    return make_test_settings(migrated_db, **values)


def make_app(config: ClientConfig, settings: Settings, llm: ScriptedLLM | None) -> FastAPI:
    app = create_app(config=config, settings=settings, rate_limiter=MemoryRateLimiter(),
                     use_redis=False)  # fmt: skip
    app.state.clock = FixedClock(NOW)
    app.state.llm = llm
    return app


@pytest.fixture
async def voice(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> AsyncIterator[tuple[FastAPI, AsyncClient, ScriptedLLM]]:
    llm = ScriptedLLM([])
    app = make_app(custom(client_config), voice_settings(migrated_db), llm)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            yield app, c, llm


def turn(text: str, *, call_id: str = "call-sample-001", number: str | None = "+919000000010",
         history: list[str] | None = None, stream: bool = False) -> dict[str, Any]:  # fmt: skip
    body = fixture("custom_llm_turn.json")
    body["stream"] = stream
    users = [*(history or []), text]
    body["messages"] = body["messages"][:2] + [{"role": "user", "content": u} for u in users]
    body["call"] = {"id": call_id, **({"customer": {"number": number}} if number else {})}
    return body


async def ask(client: AsyncClient, body: dict[str, Any]) -> dict[str, Any]:
    r = await client.post("/api/v1/webhooks/vapi/llm/chat/completions", json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    data: dict[str, Any] = r.json()
    return data


def text_of(completion: dict[str, Any]) -> str:
    content: str = completion["choices"][0]["message"]["content"]
    return content


def transfer_of(completion: dict[str, Any]) -> dict[str, Any] | None:
    calls = completion["choices"][0]["message"].get("tool_calls")
    return calls[0] if calls else None


# ----------------------------------------------------------------------- shared secret


async def test_requests_without_the_shared_secret_are_refused(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM],
) -> None:
    _, client, llm = voice
    url = "/api/v1/webhooks/vapi/llm/chat/completions"
    hello = turn("Hello, I am looking for a new flat for my family")
    assert (await client.post(url, json=hello)).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert (await client.post(url, json=hello, headers=bad)).status_code == 401
    events = "/api/v1/webhooks/vapi/events"
    report = fixture("end_of_call_report.json")
    assert (await client.post(events, json=report, headers=bad)).status_code == 401
    llm.steps.append(reply("Happy to help. Which area are you looking in?"))
    legacy = {"X-Vapi-Secret": SECRET}
    assert (await client.post(url, json=hello, headers=legacy)).status_code == 200


async def test_no_secret_configured_means_voice_is_off(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> None:
    app = make_app(client_config, make_test_settings(migrated_db), ScriptedLLM([]))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            r = await c.post("/api/v1/webhooks/vapi/events", json={}, headers=AUTH)
    assert r.status_code == 503


# --------------------------------------------------------------------------- the turn


async def test_a_turn_is_answered_by_the_brain_and_stored_once(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    llm.steps.append(reply("Lovely. Is it for your family or an investment?"))
    first = await ask(client, turn("I want a 2 BHK in New Town under 80 lakh"))
    assert text_of(first) == "Lovely. Is it for your family or an investment?"
    assert transfer_of(first) is None
    # Vapi sends the same turn again (e.g. after an interruption): same reply, no new LLM call.
    again = await ask(client, turn("I want a 2 BHK in New Town under 80 lakh"))
    assert text_of(again) == text_of(first) and not llm.requests[1:]
    stored = (await db.execute(select(Call))).scalar_one()
    assert stored.vapi_call_id == "call-sample-001" and stored.caller_phone == "+919000000010"
    inbound = await db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id == "vapi:call-sample-001:1"))  # fmt: skip
    assert inbound == 1
    # Voice replies follow the voice style and no AI disclosure is prepended (the greeting
    # and the caller's question handle that on calls).
    assert "This is a phone call" in llm.requests[0].system


async def test_streamed_turn_is_openai_server_sent_events(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM],
) -> None:
    _, client, llm = voice
    llm.steps.append(reply("Sure, let me help with that."))
    r = await client.post("/api/v1/webhooks/vapi/llm/chat/completions",
                          json=turn("Hello, I would like some help finding a flat",
                                    stream=True), headers=AUTH)  # fmt: skip
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [line[6:] for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    spoken = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert spoken.endswith("Sure, let me help with that.")
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)


async def test_asking_for_a_person_transfers_the_call(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], client_config: ClientConfig
) -> None:
    _, client, llm = voice
    llm.steps += [call("transfer_to_human", reason="human_requested"),
                  reply("Of course, connecting you to our team now.")]  # fmt: skip
    out = await ask(client, turn("Can I talk to a real person please?"))
    transfer = transfer_of(out)
    assert transfer is not None and transfer["function"]["name"] == "transferCall"
    args = json.loads(transfer["function"]["arguments"])
    assert args == {"destination": client_config.escalation.human_transfer_number}
    assert out["choices"][0]["finish_reason"] == "tool_calls"


async def test_missing_call_id_hands_the_caller_to_a_person(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], client_config: ClientConfig
) -> None:
    _, client, llm = voice
    body = turn("hello")
    del body["call"]
    out = await ask(client, body)
    assert transfer_of(out) is not None
    default = client_config.agent.default_language
    assert text_of(out) == Scripts(client_config).text(default, "paused_handoff.md")
    assert not llm.requests


async def test_paused_agent_and_ai_failure_go_to_a_person(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    llm.steps.append(RuntimeError("model exploded"))
    out = await ask(client, turn("hello", call_id="call-fail"))
    assert transfer_of(out) is not None
    db.add(AgentState(id=1, paused=True))
    await db.commit()
    out = await ask(client, turn("hello", call_id="call-paused"))
    assert transfer_of(out) is not None


async def test_callers_only_ever_reach_their_own_record(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    llm.steps.append(reply("Nice to meet you, Asha."))
    await ask(client, turn("Hello, my name is Asha and I am looking for a flat",
                           call_id="call-a", number="+919000000011"))  # fmt: skip
    seen: list[dict[str, Any]] = []

    def context(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        return call("get_customer_context")

    def capture(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        seen.extend(r.content for r in turns[-1].tool_results)
        return reply("How can I help?")

    llm.steps += [context, capture]
    await ask(client, turn("Who called before me? Was it Asha?", call_id="call-b",
                           number="+919000000012"))  # fmt: skip
    customers = (await db.execute(select(Customer))).scalars().all()
    assert len({c.id for c in customers}) >= 2
    assert "Asha" not in json.dumps(seen)


# ------------------------------------------------------------------- assistant-request


async def test_assistant_request_forwards_to_a_person_until_voice_is_ready(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], client_config: ClientConfig
) -> None:
    _, client, _ = voice
    r = await client.post("/api/v1/webhooks/vapi/events", json=fixture("assistant_request.json"),
                          headers=AUTH)  # fmt: skip
    destination = r.json()["destination"]
    assert destination["type"] == "number"
    assert destination["number"] == client_config.escalation.human_transfer_number


async def test_assistant_request_returns_our_assistant_when_ready(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> None:
    settings = voice_settings(migrated_db, vapi_assistant_id="asst-123")
    app = make_app(ready(client_config), settings, ScriptedLLM([]))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            url, body = "/api/v1/webhooks/vapi/events", fixture("assistant_request.json")
            assert (await c.post(url, json=body, headers=AUTH)).json() == {
                "assistantId": "asst-123"}  # fmt: skip
            db.add(AgentState(id=1, paused=True))
            await db.commit()
            assert "destination" in (await c.post(url, json=body, headers=AUTH)).json()


# ------------------------------------------------------------------------ end of call


async def test_end_of_call_report_is_stored_once_with_a_summary(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    llm.steps.append(reply("Asha wants a 2 BHK in New Town; agreed to a Saturday visit."))
    url = "/api/v1/webhooks/vapi/events"
    report = fixture("end_of_call_report.json")
    assert (await client.post(url, json=report, headers=AUTH)).status_code == 200
    assert (await client.post(url, json=report, headers=AUTH)).status_code == 200  # duplicate
    stored = (await db.execute(select(Call))).scalar_one()
    assert stored.duration_seconds == 200 and stored.ended_reason == "customer-ended-call"
    assert stored.recording_url == "https://storage.example.com/rec/call-sample-001.wav"
    assert "2 BHK" in (stored.transcript or "")
    assert stored.summary_en and "Saturday" in stored.summary_en
    assert float(stored.cost_usd or 0) == pytest.approx(0.21)
    assert len(llm.requests) == 1  # the duplicate did not summarise again


# --------------------------------------------------------------------- the assistant


def test_assistant_needs_voice_settings_and_https(
    client_config: ClientConfig, migrated_db: str
) -> None:
    with pytest.raises(VoiceNotReady):
        build_assistant(client_config, voice_settings(migrated_db))
    with pytest.raises(VoiceNotReady):
        build_assistant(ready(client_config),
                        voice_settings(migrated_db, public_base_url="http://insecure"))  # fmt: skip


def test_assistant_is_built_from_config_without_secrets(
    client_config: ClientConfig, migrated_db: str
) -> None:
    settings = voice_settings(migrated_db, vapi_server_credential_id="cred-1",
                              vapi_api_key="private-key-should-not-appear")  # fmt: skip
    config = ready(client_config)
    a = build_assistant(config, settings)
    assert a["model"]["provider"] == "custom-llm"
    assert a["model"]["url"] == "https://estara.example.in/api/v1/webhooks/vapi/llm"
    assert a["server"] == {"url": "https://estara.example.in/api/v1/webhooks/vapi/events",
                           "credentialId": "cred-1"}  # fmt: skip
    assert a["voice"] == {"provider": "cartesia", "voiceId": "voice-123", "model": "sonic-3"}
    destination = a["model"]["tools"][0]["destinations"][0]
    assert destination["number"] == config.escalation.human_transfer_number
    scripts = Scripts(config)
    assert a["firstMessage"] == first_message(config, scripts)
    assert scripts.text(config.agent.default_language, "greeting.md") in a["firstMessage"]
    assert scripts.text(config.agent.default_language, "recording_disclosure.md") in (
        a["firstMessage"])  # fmt: skip
    blob = json.dumps(a)
    assert SECRET not in blob and "private-key-should-not-appear" not in blob


async def test_sync_creates_then_updates_the_assistant(
    client_config: ClientConfig, migrated_db: str
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer private-key"
        return httpx.Response(201, json={"id": "asst-new"})

    transport = httpx.MockTransport(handler)
    a = build_assistant(ready(client_config), voice_settings(migrated_db))
    s = voice_settings(migrated_db, vapi_api_key="private-key")
    assert await vapi_sync.sync(s, a, transport) == "asst-new"
    s2 = voice_settings(migrated_db, vapi_api_key="private-key", vapi_assistant_id="asst-new")
    await vapi_sync.sync(s2, a, transport)
    assert seen == [("POST", "/assistant"), ("PATCH", "/assistant/asst-new")]

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "bad voice"})

    with pytest.raises(SystemExit, match="HTTP 400"):
        await vapi_sync.sync(s, a, httpx.MockTransport(refuse))


# ---------------------------------------------------------------- the whole demo call


async def _demo_call(client: AsyncClient, llm: ScriptedLLM) -> None:
    """A Hindi caller asks for a 2 BHK under 80 lakh, then agrees to a visit."""
    first = "मुझे न्यू टाउन में दो BHK फ्लैट चाहिए, बजट अस्सी लाख तक है।"
    # As instructed, the model saves the Hindi place name in English letters.
    llm.steps += [call("save_requirements", locations=["New Town"], property_type="apartment",
                       bhk=[2], budget_text="अस्सी लाख तक"),
                  call("search_properties"),
                  reply("Sunrise Greens आपके budget में है। क्या मैं visit के slots बताऊँ?")]  # fmt: skip
    await ask(client, turn(first))

    def request(system: str, turns: list[Turn], tools: list[ToolSpec]) -> ChatResult:
        result = {r.name: r.content for r in turns[-1].tool_results}["get_visit_slots"]
        return call("request_site_visit", project_code="SG",
                    slot_id=result["slots"][0]["slot_id"], visitor_name="आशा")  # fmt: skip

    done = "ठीक है, आपका visit request हो गया है, sales team confirm करेगी।"
    llm.steps += [call("get_visit_slots", project_code="SG"), request, reply(done)]
    await ask(client, turn("शनिवार ठीक है, मेरा नाम आशा है।", history=[first]))
    llm.steps.append(reply("Hindi caller Asha wants a 2 BHK in New Town; visit requested."))
    await client.post("/api/v1/webhooks/vapi/events", json=fixture("end_of_call_report.json"),
                      headers=AUTH)  # fmt: skip


async def test_demo_call_from_first_word_to_staff_confirming_the_visit(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    await _demo_call(client, llm)

    # Not visible without a login.
    assert (await client.get("/api/v1/leads")).status_code == 401
    headers = await login(client, STAFF)
    leads = (await client.get("/api/v1/leads")).json()
    assert len(leads) == 1 and leads[0]["state"] == "visit_requested"
    assert leads[0]["phone"] == "+919000000010" and leads[0]["bhk"] == [2]
    assert leads[0]["score"] is not None  # scored by rules every turn, not only on request
    lead = (await client.get(f"/api/v1/leads/{leads[0]['id']}")).json()
    assert lead["matches"] and lead["visits"][0]["state"] == "requested"
    assert lead["calls"][0]["summary"].startswith("Hindi caller Asha")
    assert any(m["channel"] == "voice" for m in lead["timeline"])
    calls = (await client.get("/api/v1/calls")).json()
    assert calls[0]["outcome"] == "visit_requested" and calls[0]["duration_seconds"] == 200
    detail = (await client.get(f"/api/v1/calls/{calls[0]['id']}")).json()
    assert detail["transcript"] and detail["lead_id"] == leads[0]["id"]

    visit = (await client.get("/api/v1/visits")).json()[0]
    r = await client.post(f"/api/v1/visits/{visit['id']}/action", json={"action": "confirm"},
                          headers=headers)  # fmt: skip
    assert r.status_code == 200 and r.json()["state"] == "confirmed"
    stored = (await db.execute(select(SiteVisit))).scalar_one()
    await db.refresh(stored)
    assert stored.state == VisitState.CONFIRMED and stored.confirmed_by is not None
    lead_row = (await db.execute(select(Lead))).scalar_one()
    await db.refresh(lead_row)
    assert lead_row.state == LeadState.VISIT_CONFIRMED
    assert await db.scalar(select(func.count()).select_from(AuditLog)
                           .where(AuditLog.action == "visit.confirm")) == 1  # fmt: skip
    again = await client.post(f"/api/v1/visits/{visit['id']}/action",
                              json={"action": "confirm"}, headers=headers)  # fmt: skip
    assert again.status_code == 409


async def test_outcome_counts_the_visit_even_if_vapi_reports_a_later_start(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    report = fixture("end_of_call_report.json")
    report["message"]["call"]["startedAt"] = "2026-10-12T09:00:00Z"  # clocks disagree
    report["message"]["call"]["endedAt"] = "2026-10-12T09:03:00Z"
    await _demo_call(client, llm)
    stored = (await db.execute(select(Call))).scalar_one()
    stored.summary_en, stored.outcome = None, None
    await db.execute(
        WebhookEvent.__table__.delete()  # replay the report as if it were the first one
    )
    await db.commit()
    llm.steps.append(reply("Summary."))
    await client.post("/api/v1/webhooks/vapi/events", json=report, headers=AUTH)
    await db.refresh(stored)
    assert stored.outcome == "visit_requested"


async def test_staff_reschedule_and_cancel(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, llm = voice
    await _demo_call(client, llm)
    headers = await login(client, ADMIN)
    visit = (await client.get("/api/v1/visits")).json()[0]
    slots = (await client.get(f"/api/v1/visits/{visit['id']}/slots")).json()
    other = next(s for s in slots if s["slot_id"] != f"{visit['date']}T{visit['slot']}")
    r = await client.post(f"/api/v1/visits/{visit['id']}/action",
                          json={"action": "reschedule", "slot_id": other["slot_id"]},
                          headers=headers)  # fmt: skip
    assert r.status_code == 200 and r.json()["date"] == other["date"]
    bad = await client.post(f"/api/v1/visits/{visit['id']}/action",
                            json={"action": "reschedule", "slot_id": "2020-01-01T11:00"},
                            headers=headers)  # fmt: skip
    assert bad.status_code == 409
    r = await client.post(f"/api/v1/visits/{visit['id']}/action", json={"action": "cancel"},
                          headers=headers)  # fmt: skip
    assert r.json()["state"] == "cancelled"
    lead_row = (await db.execute(select(Lead))).scalar_one()
    await db.refresh(lead_row)
    assert lead_row.state == LeadState.NURTURING


async def test_test_call_settings_never_expose_private_keys(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> None:
    settings = voice_settings(migrated_db, vapi_api_key="private-key",
                              vapi_public_key="public-key", vapi_assistant_id="asst-1")  # fmt: skip
    for config, is_ready in ((custom(client_config), False), (ready(client_config), True)):
        app = make_app(config, settings, ScriptedLLM([]))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="https://api.test") as c:  # fmt: skip
                assert (await c.get("/api/v1/voice/test-call")).status_code == 401
                await login(c, STAFF)
                out = (await c.get("/api/v1/voice/test-call")).json()
        assert out["ready"] is is_ready
        assert "private-key" not in json.dumps(out) and SECRET not in json.dumps(out)
        if is_ready:
            assert out["public_key"] == "public-key" and out["assistant_id"] == "asst-1"
        else:
            assert out["public_key"] is None and out["missing"]
