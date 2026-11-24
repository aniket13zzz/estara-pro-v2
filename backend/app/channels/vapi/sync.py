"""Create or update this client's Vapi assistant from config:
``python -m app.channels.vapi.sync`` (``--dry-run`` prints what would be sent, without changing
anything in Vapi).

Uses Vapi's REST API with the private ``VAPI_API_KEY`` (server side only).

- ``voice.mode: custom_llm``: ``POST /assistant`` when ``VAPI_ASSISTANT_ID`` is empty, otherwise
  ``PATCH /assistant/{id}``, with the whole assistant built from config.
- ``voice.mode: native`` (ADR 0014): the assistant is created in the Vapi dashboard, where the
  model, voice and transcriber are chosen. The sync reads it (``GET /assistant/{id}``) and
  changes only ESTARA's parts: instructions, tools, greeting, events address, call length.

Run it again after any change to the scripts, greeting, transfer number, tools or
``PUBLIC_BASE_URL``. Prints the assistant id to put in ``.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

from app.channels.vapi.assistant import VoiceNotReady, build_assistant
from app.channels.vapi.native import native_update
from app.core.config import load_config
from app.core.settings import Settings

VAPI_API = "https://api.vapi.ai"


async def sync(settings: Settings, assistant: dict[str, Any],
               transport: httpx.AsyncBaseTransport | None = None) -> str:  # fmt: skip
    async with _client(settings, transport) as client:
        if settings.vapi_assistant_id:
            response = await client.patch(f"/assistant/{settings.vapi_assistant_id}",
                                          json=assistant)  # fmt: skip
        else:
            response = await client.post("/assistant", json=assistant)
    if response.status_code >= 400:
        raise SystemExit(f"Vapi refused the assistant (HTTP {response.status_code}): "
                         f"{response.text[:1000]}")  # fmt: skip
    data = response.json()
    assistant_id = data.get("id")
    if not isinstance(assistant_id, str):
        raise SystemExit(f"Unexpected Vapi response: {json.dumps(data)[:500]}")
    return assistant_id


def _client(settings: Settings, transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    if settings.vapi_api_key is None:
        raise SystemExit("VAPI_API_KEY is not set in .env (Vapi dashboard > API Keys > private)")
    headers = {"Authorization": f"Bearer {settings.vapi_api_key.get_secret_value()}"}
    return httpx.AsyncClient(base_url=VAPI_API, headers=headers, timeout=30, transport=transport)


async def fetch_assistant(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    if not settings.vapi_assistant_id:
        raise SystemExit(
            "VAPI_ASSISTANT_ID is not set in .env. In native mode, create the assistant in the "
            "Vapi dashboard (choose the model, voice and transcriber), copy its ID into .env, "
            "then run the sync again."
        )
    async with _client(settings, transport) as client:
        response = await client.get(f"/assistant/{settings.vapi_assistant_id}")
    if response.status_code >= 400:
        raise SystemExit(f"Vapi refused to read the assistant (HTTP {response.status_code}): "
                         f"{response.text[:500]}")  # fmt: skip
    data: dict[str, Any] = response.json()
    return data


def main_native(config: Any, settings: Settings, dry_run: bool) -> int:
    existing = asyncio.run(fetch_assistant(settings))
    try:
        update = native_update(existing, config, settings)
    except VoiceNotReady as exc:
        print(f"Not ready: {exc}", file=sys.stderr)
        return 2
    if dry_run:
        print(json.dumps(update, ensure_ascii=False, indent=2))
        return 0
    assistant_id = asyncio.run(sync(settings, update))
    names = [t["function"]["name"] for t in update["model"]["tools"] if t["type"] == "function"]
    print(f"Updated Vapi assistant {assistant_id} (native mode, model "
          f"{update['model'].get('provider')}/{update['model'].get('model')} kept).")  # fmt: skip
    print(f"Tools ({len(names)} + transferCall): {', '.join(names)}")
    print(f"Tool URL:   {update['model']['tools'][0]['server']['url']}")
    print(f"Server URL: {update['server']['url']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or update the Vapi assistant.")
    parser.add_argument("--dry-run", action="store_true", help="print it, do not call Vapi")
    args = parser.parse_args(argv)
    config, settings = load_config(), Settings()
    if not settings.vapi_server_credential_id:
        print("Warning: VAPI_SERVER_CREDENTIAL_ID is empty, so Vapi will not send the shared "
              "secret and ESTARA will refuse its requests. See docs/demo.md.",
              file=sys.stderr)  # fmt: skip
    if config.voice.mode == "native":
        return main_native(config, settings, args.dry_run)
    try:
        assistant = build_assistant(config, settings)
    except VoiceNotReady as exc:
        print(f"Not ready: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(assistant, ensure_ascii=False, indent=2))
        return 0
    assistant_id = asyncio.run(sync(settings, assistant))
    action = "Updated" if settings.vapi_assistant_id else "Created"
    print(f"{action} Vapi assistant {assistant_id}.")
    if not settings.vapi_assistant_id:
        print(f"Put this in .env:  VAPI_ASSISTANT_ID={assistant_id}")
    print(f"Custom LLM URL: {assistant['model']['url']}")
    print(f"Server URL:     {assistant['server']['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
