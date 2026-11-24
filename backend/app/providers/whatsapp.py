"""WhatsApp Cloud API adapter (Meta). Business logic calls ``WhatsAppClient``, never httpx.

From Meta's docs (read 2026-11-03):
- Send: ``POST https://graph.facebook.com/<version>/<phone_number_id>/messages`` with
  ``Authorization: Bearer <access token>``; body ``{"messaging_product": "whatsapp",
  "recipient_type": "individual", "to": ..., "type": "text", "text": {"body": ...}}``.
  The response's ``messages[0].id`` is the message id later seen in status webhooks.
  Acceptance is not delivery: delivery arrives as a status webhook.
- Outside the 24-hour customer service window only approved templates may be sent:
  ``{"type": "template", "template": {"name", "language": {"code"}, "components":
  [{"type": "body", "parameters": [{"type": "text", "text": ...}]}]}}``.
- Webhook signature: ``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the body, key = app
  secret>``.

Transient failures (HTTP 429/5xx, network) are retried a few times with backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("estara.whatsapp")
GRAPH = "https://graph.facebook.com"
ATTEMPTS = 3


class WhatsAppError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class WhatsAppNotConfigured(WhatsAppError):
    pass


def valid_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256=").strip())


def to_wa(e164: str) -> str:
    """Meta takes the number in international format; digits only is the safe form."""
    return "".join(ch for ch in e164 if ch.isdigit())


@dataclass
class WhatsAppClient:
    access_token: str
    phone_number_id: str
    api_version: str = "v24.0"
    transport: httpx.AsyncBaseTransport | None = None
    sleep_base: float = 1.0

    async def _post(self, payload: dict[str, Any]) -> str:
        url = f"{GRAPH}/{self.api_version}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        last: WhatsAppError | None = None
        for attempt in range(ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
                    response = await client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last = WhatsAppError(f"network error: {type(exc).__name__}", retryable=True)
            else:
                if response.status_code < 300:
                    data = response.json()
                    message_id = ((data.get("messages") or [{}])[0]).get("id")
                    if not isinstance(message_id, str):
                        raise WhatsAppError(f"unexpected response: {str(data)[:300]}")
                    return message_id
                retryable = response.status_code == 429 or response.status_code >= 500
                last = WhatsAppError(f"HTTP {response.status_code}: {response.text[:300]}",
                                     retryable=retryable)  # fmt: skip
                if not retryable:
                    raise last
            if attempt < ATTEMPTS - 1:
                await asyncio.sleep(self.sleep_base * 2**attempt)
        assert last is not None
        raise last

    async def send_text(self, to_e164: str, body: str) -> str:
        return await self._post({
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": to_wa(to_e164), "type": "text", "text": {"preview_url": False, "body": body},
        })  # fmt: skip

    async def send_template(self, to_e164: str, name: str, language: str,
                            parameters: list[str]) -> str:  # fmt: skip
        components = (
            [{"type": "body", "parameters": [{"type": "text", "text": p} for p in parameters]}]
            if parameters
            else []
        )
        return await self._post({
            "messaging_product": "whatsapp", "recipient_type": "individual",
            "to": to_wa(to_e164), "type": "template",
            "template": {"name": name, "language": {"code": language}, "components": components},
        })  # fmt: skip


def client_from(settings: Any, transport: httpx.AsyncBaseTransport | None = None) -> WhatsAppClient:
    token = settings.whatsapp_access_token
    if token is None or not settings.whatsapp_phone_number_id:
        raise WhatsAppNotConfigured(
            "WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID are not set"
        )
    return WhatsAppClient(
        token.get_secret_value(), settings.whatsapp_phone_number_id,
        api_version=settings.whatsapp_api_version, transport=transport,
    )  # fmt: skip
