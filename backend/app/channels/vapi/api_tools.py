"""Assistant "B" (ADR 0015): ESTARA's tools as Vapi "API Request" tools, set up by one command.

    docker compose run --rm --no-deps backend python -m app.channels.vapi.api_tools

Vapi's model talks to the caller; each tool is a plain HTTPS request to
``/api/v1/webhooks/vapi/api/<tool>`` with the model's arguments as the body plus
``call_id={{ call.id }}`` and ``caller={{ customer.number }}`` (Vapi static parameters, filled in
at run time; docs.vapi.ai/tools/static-variables-and-aliases/static-parameters and
docs.vapi.ai/tools/api-request-vs-function, read 2026-11-16).

The command creates the ten tools in Vapi (or updates them, matched by name), then creates
assistant B (or updates VAPI_ASSISTANT_ID_B) with the same model, voice and transcriber as
assistant A, B's prompt, the tools, transfer and hang-up. Assistant A is only read. Run it again
after every new tunnel address.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from app.brain.scripts import Scripts
from app.brain.tools import TOOLS
from app.channels.vapi.assistant import VoiceNotReady, public_url
from app.channels.vapi.gateway import API_PATH
from app.channels.vapi.native import (
    TOOL_TIMEOUT_SECONDS,
    VAPI_TOOL_NAMES,
    native_prompt,
    native_update,
)
from app.channels.vapi.sync import _client, fetch_assistant
from app.core.config import ClientConfig, load_config
from app.core.settings import Settings

# API Request tools always wait for the answer, so B has no separate save tool: every extra
# tool call on a turn is a round trip plus a second model pass before Priya can speak.
# search_properties saves size, area and budget; the visit request takes the name.
B_TOOL_NAMES = [name for name in VAPI_TOOL_NAMES if name != "save_requirements"]
SAVE_LINE = "".join(
    [
        "- search_properties saves the size, area and budget you pass. ",
        "Call save_requirements only for\n",
        "  other needs (timeline, loan, purpose, name). It saves in the background: ",
        "keep talking, never\n",
        '  wait for it and never say "one moment" for it.\n',
    ]
)
# JSON Schema keywords Vapi lists for an API Request tool body (docs.vapi.ai/api-reference/
# tools/create). Limits such as maximum are checked again by ESTARA, so dropping them is safe.
BODY_KEYWORDS = {"type", "items", "properties", "description", "pattern", "format", "required",
                 "enum", "title"}  # fmt: skip
STATIC = [{"key": "call_id", "value": "{{ call.id }}"},
          {"key": "caller", "value": "{{ customer.number }}"}]  # fmt: skip


def prompt_b(config: ClientConfig) -> str:
    prompt = native_prompt(config, Scripts(config))
    prompt = prompt.replace(
        SAVE_LINE, "- search_properties saves the size, area and budget you pass.\n"
    )
    return prompt.replace("search_properties or\n    save_requirements", "search_properties")


def _body(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_body(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "properties":
            out[key] = {name: _body(s) for name, s in value.items()}
        elif key in BODY_KEYWORDS:
            out[key] = _body(value)
    return out


def api_tool(name: str, settings: Settings) -> dict[str, Any]:
    spec = TOOLS[name].spec
    body = _body(spec.parameters)
    body.setdefault("type", "object")
    if not body.get("properties"):
        # Vapi refuses an API Request body with no fields (HTTP 400, live run 2026-11-16).
        # ESTARA ignores this optional one.
        body["properties"] = {
            "note": {"type": "string", "description": "Not needed. Leave it out."}
        }
    tool: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "method": "POST",
        # The call id twice: as a static body field and in the URL, both filled in by Vapi.
        "url": public_url(settings) + f"{API_PATH}/{spec.name}?call_id={{{{ call.id }}}}",
        "body": body,
        "parameters": STATIC,
        "timeoutSeconds": TOOL_TIMEOUT_SECONDS,
        # No spoken "this will take a sec" while the request runs.
        "messages": [{"type": "request-start", "content": ""}],
    }
    if settings.vapi_server_credential_id:
        tool["credentialId"] = settings.vapi_server_credential_id
    return tool


def assistant_b(existing_a: dict[str, Any], config: ClientConfig, settings: Settings,
                tool_ids: list[str]) -> dict[str, Any]:  # fmt: skip
    """Assistant B: A's model, voice and transcriber; B's prompt and API Request tools."""
    update = native_update(existing_a, config, settings)
    model = update["model"]
    model["messages"] = [{"role": "system", "content": prompt_b(config)}]
    model["tools"] = [t for t in model["tools"] if t["type"] != "function"]  # transfer, endCall
    model["toolIds"] = tool_ids
    out = {**update, "name": f"{config.agent.name} B"[:40], "model": model}
    for key in ("voice", "transcriber"):
        if existing_a.get(key):
            out[key] = existing_a[key]
    return out


def _check(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise SystemExit(f"Vapi refused {what} (HTTP {response.status_code}): "
                         f"{response.text[:800]}")  # fmt: skip
    data: dict[str, Any] = response.json()
    return data


async def sync_b(
    config: ClientConfig, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> tuple[str, bool, list[str], bool]:
    existing_a = await fetch_assistant(settings, transport)
    async with _client(settings, transport) as client:
        listed: Any = (await client.get("/tool", params={"limit": 1000})).json()
        if isinstance(listed, dict):
            listed = listed.get("results", [])
        ours = {t.get("name"): t["id"] for t in listed if isinstance(t, dict)
                if t.get("type") == "apiRequest" and API_PATH in str(t.get("url", ""))}  # fmt: skip
        tool_ids: list[str] = []
        for name in B_TOOL_NAMES:
            tool = api_tool(name, settings)
            if name in ours:
                _check(await client.patch(f"/tool/{ours[name]}", json=tool), f"the tool {name}")
                tool_ids.append(ours[name])
            else:
                made = _check(await client.post("/tool", json={"type": "apiRequest", **tool}),
                              f"the tool {name}")  # fmt: skip
                tool_ids.append(made["id"])
        created = not settings.vapi_assistant_id_b
        voice_copied = True
        if created:
            body = assistant_b(existing_a, config, settings, tool_ids)
            response = await client.post("/assistant", json=body)
            # Live run 2026-11-16: Vapi refused A's Cartesia voice id on create ("Couldn't find
            # Cartesia voice") although A uses it. Create B without it; the owner picks the same
            # voice in the dashboard, and later runs leave B's voice alone.
            if response.status_code == 400 and "voice" in response.text.lower():
                body.pop("voice", None)
                voice_copied = False
                response = await client.post("/assistant", json=body)
            data = _check(response, "assistant B")
        else:
            path = f"/assistant/{settings.vapi_assistant_id_b}"
            existing_b = _check(await client.get(path), "to read assistant B")
            body = assistant_b(existing_b, config, settings, tool_ids)
            for key in ("voice", "transcriber"):  # the dashboard choices on B stay
                body.pop(key, None)
            data = _check(await client.patch(path, json=body), "assistant B")
    return str(data["id"]), created, tool_ids, voice_copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or update assistant B in Vapi.")
    parser.add_argument("--dry-run", action="store_true", help="print it, do not call Vapi")
    args = parser.parse_args(argv)
    config, settings = load_config(), Settings()
    if args.dry_run:
        tools = [api_tool(name, settings) for name in B_TOOL_NAMES]
        print(json.dumps({"tools": tools, "prompt": prompt_b(config)}, ensure_ascii=False,
                         indent=2))  # fmt: skip
        return 0
    try:
        assistant_id, created, tool_ids, voice_copied = asyncio.run(sync_b(config, settings))
    except VoiceNotReady as exc:
        print(f"Not ready: {exc}", file=sys.stderr)
        return 2
    if created:
        copied = "model, voice and transcriber" if voice_copied else "model and transcriber"
        print(f"Created assistant B {assistant_id} with {len(tool_ids)} API Request tools "
              f"({copied} copied from A).")  # fmt: skip
        if not voice_copied:
            print("Vapi did not accept A's voice for a new assistant. In the Vapi dashboard open "
                  "assistant B > Voice, pick the same voice as A and click Publish.")  # fmt: skip
    else:
        print(f"Updated assistant B {assistant_id} with {len(tool_ids)} API Request tools "
              "(its model, voice and transcriber kept).")  # fmt: skip
    print(f"Tool URL: {public_url(settings)}{API_PATH}/<tool>")
    if created:
        print(f"Next: put VAPI_ASSISTANT_ID_B={assistant_id} in .env, so later runs update it,")
        print(f'and assistantIdB: "{assistant_id}" in demo-site/public/config.js.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
