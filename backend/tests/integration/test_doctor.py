"""The demo check names the broken link: an old tunnel address on an assistant, a missing
credential, and what each tool call in Vapi's last call got back."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.channels.vapi import doctor
from tests.integration.test_native_voice import native  # noqa: F401
from tests.integration.test_voice import voice_settings

BASE = "https://estara.example.in"


@pytest.mark.usefixtures("inventory")
async def test_a_tool_request_through_the_tunnel_finds_homes(
    native: tuple[FastAPI, AsyncClient],  # noqa: F811
    migrated_db: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app, _ = native
    doctor.failures.clear()
    settings = app.state.settings
    await doctor.check_tunnel(settings, BASE, httpx.ASGITransport(app=app))
    out = capsys.readouterr().out
    assert "reaches ESTARA" in out and "found 3 homes" in out and not doctor.failures
    wrong = settings.model_copy(update={"vapi_webhook_secret": None})
    await doctor.check_tunnel(wrong, BASE, httpx.ASGITransport(app=app))
    assert doctor.failures  # a refused or failed request is reported, never a PASS


def test_old_addresses_and_the_last_call_are_shown(
    migrated_db: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body: Any
        if path == "/assistant/asst-a":
            body = {
                "name": "Sara",
                "server": {"url": f"{BASE}/api/v1/webhooks/vapi/events"},
                "model": {
                    "tools": [
                        {
                            "type": "function",
                            "server": {
                                "url": f"{BASE}/api/v1/webhooks/vapi/tools",
                                "credentialId": "cred-1",
                            },
                        }
                    ]
                },
            }
        elif path == "/assistant/asst-b":
            body = {
                "name": "Priya B",
                "server": {"url": "https://old-words.trycloudflare.com/x"},
                "model": {"toolIds": ["t1"]},
            }
        elif path == "/tool/t1":
            body = {
                "url": "https://old-words.trycloudflare.com/api/v1/webhooks/vapi/api/x",
                "credentialId": "cred-1",
            }
        elif path == "/call":
            body = [
                {
                    "assistantId": "asst-other",
                    "endedReason": "customer-ended-call",
                    "artifact": {
                        "messages": [
                            {
                                "role": "tool_calls",
                                "toolCalls": [
                                    {"function": {"name": "search_properties", "arguments": "{}"}}
                                ],
                            },
                            {
                                "role": "tool_call_result",
                                "name": "search_properties",
                                "result": "Request failed: 530",
                            },
                        ],
                        "performanceMetrics": {"modelLatencyAverage": 2357},
                    },
                }
            ]
        else:
            return httpx.Response(404)
        return httpx.Response(200, json=body)

    doctor.failures.clear()
    settings = voice_settings(
        migrated_db,
        vapi_api_key="k",
        vapi_assistant_id="asst-a",
        vapi_assistant_id_b="asst-b",
        vapi_server_credential_id="cred-1",
    )
    import asyncio

    asyncio.run(doctor.check_vapi(settings, BASE, httpx.MockTransport(handler)))
    out = capsys.readouterr().out
    assert "PASS  assistant A (Sara): 1 tools point at estara.example.in" in out
    assert (
        "assistant B (Priya B): tools point at an old address (old-words.trycloudflare.com)" in out
    )
    assert "<- search_properties: Request failed: 530" in out
    assert "the last call used assistant asst-other, which ESTARA does not set up" in out
    assert "1 tool calls in the last call failed in Vapi" in out
    assert "slow reply: modelLatencyAverage=2357 ms" in out
    assert "k" not in [w for w in out.split()]  # the key is never printed
