"""Assistant B (ADR 0015): ESTARA's tools as Vapi "API Request" tools, tied to the call by
call_id={{ call.id }} in the URL."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.vapi.api_tools import B_TOOL_NAMES, prompt_b, sync_b
from app.core.config import ClientConfig
from app.db.models import Call, SiteVisit
from tests.integration.test_native_voice import _existing, native  # noqa: F401
from tests.integration.test_voice import AUTH, SECRET, voice_settings

URL = "/api/v1/webhooks/vapi/api"
pytestmark = pytest.mark.usefixtures("inventory")


async def test_a_search_is_tied_to_its_call(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, client = native
    r = await client.post(f"{URL}/search_properties?call_id=call-b-1&caller= 919000000011",
                          json={"locations": ["New Town"], "bhk": [2], "budget_text": "80 lakh"},
                          headers=AUTH)  # fmt: skip
    assert r.status_code == 200
    assert r.json()["matches"]
    call = (await db.execute(select(Call))).scalar_one()
    assert call.vapi_call_id == "call-b-1"
    assert call.caller_phone == "+919000000011"  # the "+" a query string turned into a space


async def test_the_call_id_can_also_come_in_the_body(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
) -> None:
    _, client = native
    body = {"project_code": "SG", "call_id": "call-b-2"}
    r = await client.post(f"{URL}/get_visit_slots", json=body, headers=AUTH)
    slot_id = r.json()["slots"][0]["slot_id"]
    visit = {"project_code": "SG", "slot_id": slot_id, "visitor_name": "Asha", "visitors": 2}
    await client.post(f"{URL}/save_requirements?call_id=call-b-2", headers=AUTH,
                      json={"locations": ["New Town"], "bhk": [2], "budget_text": "80 lakh",
                            "property_type": "apartment", "timeline_months": 3})  # fmt: skip
    done = await client.post(f"{URL}/request_site_visit?call_id=call-b-2", json=visit,
                             headers=AUTH)  # fmt: skip
    assert done.json()["status"] == "requested", done.json()
    assert await db.scalar(select(func.count()).select_from(SiteVisit)) == 1


@pytest.mark.parametrize("query", ["", "?call_id=", "?call_id={{ call.id }}"])
async def test_no_call_id_means_nothing_is_saved(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    db: AsyncSession,
    query: str,
) -> None:
    _, client = native
    r = await client.post(f"{URL}/search_properties{query}", json={}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["matches"]  # a lookup still answers (v1.9.0), on a rolled-back record
    visit = {"project_code": "SG", "slot_id": "x", "visitor_name": "Asha"}
    r = await client.post(f"{URL}/request_site_visit{query}", json=visit, headers=AUTH)
    assert r.json()["status"] == "not_saved"  # a write is refused, never "booked"
    assert await db.scalar(select(func.count()).select_from(Call)) == 0


async def test_needs_the_secret_and_a_real_tool(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
) -> None:
    _, client = native
    r = await client.post(f"{URL}/search_properties?call_id=x", json={})
    assert r.status_code == 401
    r = await client.post(f"{URL}/evaluate_lead?call_id=x", json={}, headers=AUTH)
    assert r.json()["status"] == "error"  # hidden tools stay hidden


async def test_one_command_builds_assistant_b_from_a(
    client_config: ClientConfig, migrated_db: str
) -> None:
    sent: list[tuple[str, str, dict[str, Any] | None]] = []
    existing_tools = [{"id": "tool-old-search", "type": "apiRequest", "name": "search_properties",
                       "url": "https://old.trycloudflare.com/api/v1/webhooks/vapi/api/x"},
                      {"id": "someone-elses", "type": "apiRequest", "name": "get_visit_slots",
                       "url": "https://other.example.com/slots"}]  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer private-key"
        body = json.loads(request.content) if request.content else None
        sent.append((request.method, request.url.path, body))
        if request.method == "GET" and request.url.path == "/tool":
            return httpx.Response(200, json=existing_tools)
        if request.method == "GET":
            return httpx.Response(200, json=_existing())
        if request.url.path == "/tool":
            return httpx.Response(201, json={"id": f"tool-{body['name']}"})
        return httpx.Response(200, json={"id": "asst-b"})

    settings = voice_settings(migrated_db, vapi_api_key="private-key",
                              vapi_assistant_id="asst-native",
                              vapi_server_credential_id="cred-1")  # fmt: skip
    assistant_id, created, tool_ids, voice_copied = await sync_b(
        client_config, settings, httpx.MockTransport(handler)
    )
    assert (assistant_id, created, voice_copied) == ("asst-b", True, True)
    assert len(tool_ids) == len(B_TOOL_NAMES) and "tool-old-search" in tool_ids
    assert "save_requirements" not in B_TOOL_NAMES  # no blocking save round trip in B
    assert "someone-elses" not in tool_ids  # only ESTARA's own tools are reused
    calls = [(m, path) for m, path, _ in sent]
    assert calls[0] == ("GET", "/assistant/asst-native")  # A is only read
    assert ("PATCH", "/assistant/asst-native") not in calls
    assert ("PATCH", "/tool/tool-old-search") in calls
    made = [b for m, path, b in sent if m == "POST" and path == "/tool"]
    assert len(made) == len(B_TOOL_NAMES) - 1
    for tool in made:
        assert tool["type"] == "apiRequest" and tool["method"] == "POST"
        assert tool["url"] == (
            f"https://estara.example.in{URL}/{tool['name']}?call_id={{{{ call.id }}}}"
        )
        assert {"key": "call_id", "value": "{{ call.id }}"} in tool["parameters"]
        assert tool["credentialId"] == "cred-1" and tool["timeoutSeconds"] == 10
        assert "maximum" not in json.dumps(tool["body"])  # only keywords Vapi lists
        assert tool["body"]["properties"]  # Vapi refuses a body with no fields
    [(_, _, b)] = [x for x in sent if x[0] == "POST" and x[1] == "/assistant"]
    assert b["model"]["toolIds"] == tool_ids
    assert [t["type"] for t in b["model"]["tools"]] == ["transferCall", "endCall"]
    assert (b["model"]["provider"], b["model"]["model"]) == ("openai", "gpt-6-luna")
    assert b["voice"] == _existing()["voice"] and b["transcriber"] == _existing()["transcriber"]
    prompt = b["model"]["messages"][0]["content"]
    assert prompt == prompt_b(client_config)
    assert "background" not in prompt  # API Request tools always wait for the answer
    assert "Privacy" in prompt and "endCall" in prompt
    assert SECRET not in json.dumps(sent) and "private-key" not in json.dumps(sent)


async def test_b_is_created_without_a_voice_vapi_refuses_and_updates_keep_b_choices(
    client_config: ClientConfig, migrated_db: str
) -> None:
    posts: list[dict[str, Any]] = []
    patched: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if request.method == "GET" and path == "/tool":
            return httpx.Response(200, json=[])
        if request.method == "GET":
            return httpx.Response(200, json=_existing())
        if path == "/tool":
            return httpx.Response(201, json={"id": f"tool-{body['name']}"})
        if request.method == "POST":
            posts.append(body)
            if "voice" in body:  # what Vapi answered on the live run
                return httpx.Response(400, json={"message": "Couldn't Find Cartesia Voice `x`."})
            return httpx.Response(201, json={"id": "asst-b"})
        patched.append(body)
        return httpx.Response(200, json={"id": "asst-b"})

    settings = voice_settings(migrated_db, vapi_api_key="k", vapi_assistant_id="asst-native")
    transport = httpx.MockTransport(handler)
    _, created, _, voice_copied = await sync_b(client_config, settings, transport)
    assert created and not voice_copied
    assert len(posts) == 2 and "voice" not in posts[1] and "transcriber" in posts[1]
    later = voice_settings(migrated_db, vapi_api_key="k", vapi_assistant_id="asst-native",
                           vapi_assistant_id_b="asst-b")  # fmt: skip
    _, created, _, _ = await sync_b(client_config, later, transport)
    assert not created
    assert "voice" not in patched[-1] and "transcriber" not in patched[-1]
    assert patched[-1]["model"]["toolIds"]
