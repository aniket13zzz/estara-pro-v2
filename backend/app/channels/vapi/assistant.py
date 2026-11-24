"""The ONE Vapi assistant for this client, built from ``client.config.yaml`` (never hand-edited).

Field names follow Vapi's docs as read on 2026-11-03:
- ``model.provider = "custom-llm"`` with ``model.url``: Vapi calls ``{url}/chat/completions``
  for every caller turn (docs.vapi.ai/assistants/custom-llm, /custom-llm).
- ``voice = {"provider": "cartesia", "voiceId": ..., "model": ...}``.
- ``server = {"url": ..., "credentialId": ...}`` receives server messages
  (docs.vapi.ai/server-url/setting-server-urls).
- A ``transferCall`` tool with one ``number`` destination: the client's human transfer number
  (docs.vapi.ai/tools/transfer-call). Whether a custom LLM can trigger it is not documented;
  this is a live check (docs/demo.md). If it cannot, the caller is told the team will call back
  and the handoff is already recorded, so no lead is lost.

The first message is the greeting (both languages in bilingual mode) and the recording notice,
from ``client/scripts``. Nothing here is a secret: the shared secret lives in Vapi's credential
store, referenced by id.
"""

from __future__ import annotations

from typing import Any

from app.brain.scripts import Scripts
from app.core.config import ClientConfig
from app.core.settings import Settings

LLM_PATH = "/api/v1/webhooks/vapi/llm"  # Vapi appends /chat/completions
EVENTS_PATH = "/api/v1/webhooks/vapi/events"
MODEL_NAME = "estara-sales-brain"


class VoiceNotReady(Exception):
    """Voice fields are null in config, or the public URL is missing: no live assistant."""


def voice_ready(config: ClientConfig, settings: Settings) -> bool:
    """Calls can reach the agent: the assistant exists, the shared secret is set, and in
    custom_llm mode every voice field is set. Otherwise every call goes to a person."""
    if not settings.vapi_assistant_id or not settings.vapi_webhook_secret:
        return False
    return config.voice.mode == "native" or config.voice.custom_ready


def first_language_order(config: ClientConfig) -> list[str]:
    first = config.agent.default_language
    others = [lang for lang in config.agent.languages if lang != first]
    return [first, *others] if config.agent.greeting_mode == "bilingual" else [first]


def first_message(config: ClientConfig, scripts: Scripts) -> str:
    """Greeting then recording notice, in each greeting language, so every caller understands
    the notice in the language they were greeted in."""
    parts = []
    for lang in first_language_order(config):
        parts += [scripts.text(lang, "greeting.md"), scripts.text(lang, "recording_disclosure.md")]
    return " ".join(parts)


def public_url(settings: Settings) -> str:
    base = (settings.public_base_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise VoiceNotReady(
            "PUBLIC_BASE_URL must be the https address Vapi uses to reach this backend "
            "(your tunnel URL for a demo, the client's domain in production)"
        )
    return base


def transfer_tool(config: ClientConfig, scripts: Scripts) -> dict[str, Any]:
    """Vapi's own transfer tool, with the one number in config as its only destination."""
    return {
        "type": "transferCall",
        "destinations": [
            {
                "type": "number",
                "number": config.escalation.human_transfer_number,
                "description": "The company's sales team.",
                "message": scripts.text(config.agent.default_language, "voice_transfer.md"),
            }
        ],
    }


def build_assistant(config: ClientConfig, settings: Settings) -> dict[str, Any]:
    if not config.voice.custom_ready:
        raise VoiceNotReady(
            "voice.tts (model, voice_id) and voice.stt (provider, model) must be set in "
            "client.config.yaml before a live assistant is built"
        )
    base = public_url(settings)
    scripts = Scripts(config)
    tts, stt = config.voice.tts, config.voice.stt
    server: dict[str, Any] = {"url": base + EVENTS_PATH}
    if settings.vapi_server_credential_id:
        server["credentialId"] = settings.vapi_server_credential_id
    return {
        "name": f"ESTARA - {config.company.name}"[:40],
        "firstMessage": first_message(config, scripts),
        "model": {
            "provider": "custom-llm",
            "url": base + LLM_PATH,
            "model": MODEL_NAME,
            # Vapi requires a system message; the real instructions are built per turn by the
            # Sales Brain on our side, so this one only says where replies come from.
            "messages": [{"role": "system", "content": "Replies come from the ESTARA backend."}],
            "tools": [transfer_tool(config, scripts)],
        },
        "voice": {"provider": tts.provider, "voiceId": tts.voice_id, "model": tts.model},
        "transcriber": {"provider": stt.provider, "model": stt.model, "language": stt.language},
        "server": server,
        "maxDurationSeconds": config.voice.max_call_minutes * 60,
    }


def human_transfer_destination(config: ClientConfig) -> dict[str, Any]:
    """An ``assistant-request`` answer that forwards the call straight to a person."""
    scripts = Scripts(config)
    return {
        "destination": {
            "type": "number",
            "number": config.escalation.human_transfer_number,
            "message": scripts.text(config.agent.default_language, "voice_transfer.md"),
        }
    }
