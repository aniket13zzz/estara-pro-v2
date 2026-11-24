"""Native voice mode (ADR 0014): Vapi's model calls ESTARA's tools through one gateway."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.scripts import Scripts
from app.channels.vapi import sync as vapi_sync
from app.channels.vapi.assistant import VoiceNotReady
from app.channels.vapi.native import VAPI_TOOL_NAMES, native_prompt, native_update
from app.core.config import ClientConfig
from app.db.models import AgentState, Call, Customer, Lead, Message, SiteVisit, VapiToolCall
from app.handoff.service import in_working_hours
from app.providers.embeddings.local import HashingEmbedder
from tests.conftest import NOW
from tests.fakes import ScriptedLLM
from tests.integration.test_voice import AUTH, SECRET, fixture, make_app, voice_settings

pytestmark = pytest.mark.usefixtures("inventory")
TOOLS_URL = "/api/v1/webhooks/vapi/tools"


@pytest.fixture
async def native(
    migrated_db: str, client_config: ClientConfig
) -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    assert client_config.voice.mode == "native"
    settings = voice_settings(migrated_db, vapi_assistant_id="asst-native")
    app = make_app(client_config, settings, ScriptedLLM([]))
    app.state.embedder = HashingEmbedder()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            yield app, c


def tool_request(*calls: tuple[str, str, dict[str, Any]], call_id: str = "call-native-001",
                 number: str | None = "+919000000010",
                 said: list[tuple[str, str]] | None = None) -> dict[str, Any]:  # fmt: skip
    body = copy.deepcopy(fixture("tool_calls.json"))
    message = body["message"]
    message["toolCallList"] = [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}
        for cid, name, args in calls
    ]
    message["call"] = {"id": call_id, **({"customer": {"number": number}} if number else {})}
    if said is not None:
        message["artifact"]["messages"] = [{"role": r, "message": t} for r, t in said]
    return body


async def use(client: AsyncClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    r = await client.post(TOOLS_URL, json=body, headers=AUTH)
    assert r.status_code == 200, r.text
    out: list[dict[str, Any]] = r.json()["results"]
    return out


def result_of(entry: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in entry, entry
    parsed: dict[str, Any] = json.loads(entry["result"])
    return parsed


# ------------------------------------------------------------------------ the gateway


async def test_tool_calls_need_the_shared_secret(native: tuple[FastAPI, AsyncClient]) -> None:
    _, client = native
    body = tool_request(("t1", "get_customer_context", {}))
    assert (await client.post(TOOLS_URL, json=body)).status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert (await client.post(TOOLS_URL, json=body, headers=wrong)).status_code == 401


async def test_caller_words_and_tools_build_the_lead(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    said = [("bot", "Hello, thank you for calling."),
            ("user", "I want a 2 BHK in New Town, budget up to 80 lakh")]  # fmt: skip
    [context] = await use(client, tool_request(("t1", "get_customer_context", {}), said=said))
    # The fixed rules read the caller's words before any tool ran.
    assert "bhk" in result_of(context)["requirements_known"]
    [search] = await use(client, tool_request(("t2", "search_properties", {}), said=said))
    found = result_of(search)
    assert found["matches"], "approved projects match"
    assert found["visit_slots"]["slots"], "slots come with the search"
    lead = (await db.execute(select(Lead))).scalar_one()
    await db.refresh(lead)
    assert lead.score is not None  # re-scored by the backend rules, not by the model
    stored = (await db.execute(select(Message.provider_message_id))).scalars().all()
    assert sorted(p for p in stored if p) == ["vapi:call-native-001:1",
                                              "vapi:call-native-001:agent:1"]  # fmt: skip
    in_order = (await db.execute(select(Message.content).order_by(Message.created_at))).scalars()
    assert list(in_order) == [text for _, text in said]  # the timeline reads as spoken
    call = (await db.execute(select(Call))).scalar_one()
    assert call.caller_phone == "+919000000010"


async def test_a_repeated_tool_call_books_one_visit(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    said = [("user", "I want a 2 BHK flat in New Town, budget up to 80 lakh")]
    [slots] = await use(client, tool_request(("t1", "get_visit_slots", {"project_code": "SG"}),
                                             said=said))  # fmt: skip
    slot_id = result_of(slots)["slots"][0]["slot_id"]
    visit = ("t2", "request_site_visit",
             {"project_code": "SG", "slot_id": slot_id, "visitor_name": "Asha"})  # fmt: skip
    [first] = await use(client, tool_request(visit, said=said))
    assert result_of(first)["status"] == "requested"
    [again] = await use(client, tool_request(visit, said=said))  # Vapi retries the same call
    assert result_of(first) == result_of(again)  # the stored first answer
    assert await db.scalar(select(func.count()).select_from(SiteVisit)) == 1
    assert await db.scalar(select(func.count()).select_from(VapiToolCall)) == 2


async def test_callers_only_reach_their_own_record(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    await use(client, tool_request(
        ("a1", "save_requirements", {"customer_name": "Asha", "locations": ["New Town"]}),
        call_id="call-a", number="+919000000011"))  # fmt: skip
    [other] = await use(client, tool_request(("b1", "get_customer_context", {}),
                                             call_id="call-b", number="+919000000012"))  # fmt: skip
    assert "Asha" not in json.dumps(result_of(other))
    assert await db.scalar(select(func.count()).select_from(Customer)) == 2


async def test_transfer_follows_working_hours(
    native: tuple[FastAPI, AsyncClient], client_config: ClientConfig
) -> None:
    _, client = native
    [out] = await use(client, tool_request(("t1", "transfer_to_human",
                                            {"reason": "human_requested"})))  # fmt: skip
    result = result_of(out)
    assert in_working_hours(client_config, NOW)
    assert result["transfer_now"] is True
    assert result["say"] == Scripts(client_config).text("en", "voice_transfer.md")


async def test_paused_agent_only_hands_over(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    db.add(AgentState(id=1, paused=True, paused_reason="holiday"))
    await db.commit()
    [out] = await use(client, tool_request(("t1", "search_properties", {})))
    assert result_of(out)["status"] == "agent_paused"
    [handover] = await use(client, tool_request(("t2", "transfer_to_human",
                                                 {"reason": "human_requested"})))  # fmt: skip
    assert result_of(handover)["status"] == "handed_off"


async def test_unknown_tools_and_missing_call_id_are_safe_errors(
    native: tuple[FastAPI, AsyncClient],
) -> None:
    _, client = native
    [out] = await use(client, tool_request(("t1", "list_all_leads", {})))
    assert "Unknown tool" in out["error"]
    body = tool_request(("t2", "get_customer_context", {}))
    del body["message"]["call"]
    [out] = await use(client, body)
    assert "team will confirm" in out["error"] and "Do not hand over" in out["error"]


async def test_end_of_call_flags_numbers_no_tool_gave(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    said = [("user", "What does a 2 BHK in Sunrise Greens cost? My budget is 80 lakh"),
            ("bot", "The 2 BHK starts at 95 lakh, and your 80 lakh budget is close.")]  # fmt: skip
    await use(client, tool_request(("t1", "get_customer_context", {}), said=said))
    report = copy.deepcopy(fixture("end_of_call_report.json"))
    report["message"]["call"]["id"] = "call-native-001"
    report["message"]["artifact"]["messages"] = [{"role": r, "message": t} for r, t in said]
    r = await client.post("/api/v1/webhooks/vapi/events", json=report, headers=AUTH)
    assert r.status_code == 200
    call = (await db.execute(select(Call))).scalar_one()
    await db.refresh(call)
    assert call.unverified_numbers == ["95"]  # "80" came from the caller, "2" is ignored
    agent_turns = await db.scalar(select(func.count()).select_from(Message).where(
        Message.provider_message_id == "vapi:call-native-001:agent:1"))  # fmt: skip
    assert agent_turns == 1


async def test_a_call_without_tool_calls_still_gets_a_lead(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession
) -> None:
    _, client = native
    report = copy.deepcopy(fixture("end_of_call_report.json"))
    report["message"]["call"] = {"id": "call-quiet", "customer": {"number": "+919000000013"}}
    report["message"]["artifact"]["messages"] = [
        {"role": "user", "message": "Do you have a 3 BHK in Rajarhat?"}]  # fmt: skip
    await client.post("/api/v1/webhooks/vapi/events", json=report, headers=AUTH)
    assert await db.scalar(select(func.count()).select_from(Lead)) == 1
    assert await db.scalar(select(func.count()).select_from(Message)) == 1


async def test_assistant_request_uses_the_native_assistant(
    native: tuple[FastAPI, AsyncClient],
) -> None:
    _, client = native
    r = await client.post("/api/v1/webhooks/vapi/events", json=fixture("assistant_request.json"),
                          headers=AUTH)  # fmt: skip
    assert r.json() == {"assistantId": "asst-native"}


# ------------------------------------------------------------------------ the sync


def _existing(provider: str = "openai") -> dict[str, Any]:
    return {"id": "asst-native", "name": "Sara",
            "model": {"provider": provider, "model": "gpt-6-luna", "temperature": 0.5,
                      "toolIds": ["hand-made-tool"]},
            "voice": {"provider": "cartesia", "voiceId": "aadhya"},
            "transcriber": {"provider": "soniox", "model": "stt-rt-v5"}}  # fmt: skip


async def test_sync_keeps_the_dashboard_choices_and_adds_estaras_tools(
    client_config: ClientConfig, migrated_db: str
) -> None:
    sent: list[tuple[str, dict[str, Any] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer private-key"
        body = json.loads(request.content) if request.content else None
        sent.append((request.method, body))
        return httpx.Response(200, json=_existing() if request.method == "GET"
                              else {"id": "asst-native"})  # fmt: skip

    transport = httpx.MockTransport(handler)
    settings = voice_settings(migrated_db, vapi_api_key="private-key",
                              vapi_assistant_id="asst-native",
                              vapi_server_credential_id="cred-1")  # fmt: skip
    existing = await vapi_sync.fetch_assistant(settings, transport)
    update = native_update(existing, client_config, settings)
    assert await vapi_sync.sync(settings, update, transport) == "asst-native"
    assert [m for m, _ in sent] == ["GET", "PATCH"]
    model = update["model"]
    kept = (model["provider"], model["model"], model["temperature"])
    assert kept == ("openai", "gpt-6-luna", 0.5)
    assert "voice" not in update and "transcriber" not in update  # dashboard choices stay
    assert model["toolIds"] == []
    names = [t["function"]["name"] for t in model["tools"] if t["type"] == "function"]
    assert names == VAPI_TOOL_NAMES and "evaluate_lead" not in names
    background = [t["function"]["name"] for t in model["tools"] if t.get("async")]
    assert background == ["save_requirements"]  # Sara never waits for a save
    assert model["tools"][-1]["type"] == "endCall"  # Sara can hang up when the caller says bye
    for tool in model["tools"][:-2]:
        assert tool["server"] == {"url": "https://estara.example.in/api/v1/webhooks/vapi/tools",
                                  "timeoutSeconds": 10, "credentialId": "cred-1"}  # fmt: skip
        assert tool["messages"] == [{"type": "request-start", "content": ""}]  # no filler
    transfer = model["tools"][-2]
    assert transfer["type"] == "transferCall"
    assert transfer["destinations"][0]["number"] == client_config.escalation.human_transfer_number
    assert transfer["destinations"][0]["transferPlan"]["fallbackPlan"]["endCallEnabled"] is False
    assert SECRET not in json.dumps(update) and "private-key" not in json.dumps(update)


def test_sync_refuses_an_assistant_without_a_model(
    client_config: ClientConfig, migrated_db: str
) -> None:
    settings = voice_settings(migrated_db, vapi_assistant_id="asst-native")
    with pytest.raises(VoiceNotReady, match="choose a model"):
        native_update(_existing(provider="custom-llm"), client_config, settings)


def test_native_prompt_has_the_rules_scripts_and_no_private_data(
    client_config: ClientConfig,
) -> None:
    prompt = native_prompt(client_config, Scripts(client_config))
    scripts = Scripts(client_config)
    for lang in client_config.agent.languages:
        assert scripts.sales_script(lang) in prompt
        assert scripts.objection(lang, "price") in prompt
    assert "get_customer_context" in prompt and "Privacy" in prompt
    assert "Never say dollars" in prompt  # transcribers turn "80 lakh" into "$80,000"
    assert "Reply only in the reply language named above" not in prompt
    assert "rakh dijiye" in prompt and "endCall" in prompt
    assert "Before a lookup you may say" not in prompt
    private = [client_config.escalation.human_transfer_number]
    for person in client_config.sales_team:
        private += [person.phone, person.email]
    for value in private:
        assert value not in prompt


async def test_retention_deletes_old_tool_calls(
    native: tuple[FastAPI, AsyncClient], db: AsyncSession, client_config: ClientConfig
) -> None:
    from datetime import timedelta

    from app.jobs.retention import apply_retention

    _, client = native
    await use(client, tool_request(("t1", "save_requirements", {"customer_name": "Asha"})))
    created = (await db.execute(select(VapiToolCall))).scalar_one().created_at
    assert (await apply_retention(db, client_config, created))["tool_calls"] == 0
    later = created + timedelta(days=client_config.retention.transcript_days + 1)
    assert (await apply_retention(db, client_config, later))["tool_calls"] == 1
    assert await db.scalar(select(func.count()).select_from(VapiToolCall)) == 0


async def test_nothing_fits_offers_real_alternatives(native: tuple[FastAPI, AsyncClient]) -> None:
    """3 BHK in New Town under 80 lakh does not exist in the sample data: offer the next budget
    step for a 3 BHK and a 2 BHK within budget, each labelled honestly."""
    _, client = native
    said = [("user", "I want a 3 BHK flat in New Town, budget up to 80 lakh")]
    [out] = await use(client, tool_request(("t1", "search_properties", {}), said=said))
    result = result_of(out)
    assert result["matches"] == []
    alternatives = result["alternatives"]
    by_kind = {a["differs"] for a in alternatives}
    assert by_kind == {"budget", "size"}
    assert [a["differs"] for a in alternatives][:2] == ["budget", "size"]
    nearest_size = next(a for a in alternatives if a["differs"] == "size")
    assert nearest_size["bhk"] == 2  # one size down, within budget, in New Town
    for a in alternatives:
        assert a["price"], "every alternative has an approved price"
        if a["differs"] == "budget":
            assert a["bhk"] == 3
        else:
            assert a["bhk"] != 3
    assert "Never say an alternative matches" in result["instruction"]
    # The next slots come with the answer, so inviting a visit needs no second lookup.
    assert result["visit_slots"]["project_code"] == alternatives[0]["project_code"]
    assert len(result["visit_slots"]["slots"]) == 2
