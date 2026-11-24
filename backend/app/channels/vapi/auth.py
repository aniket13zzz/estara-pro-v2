"""Shared-secret check for every request from Vapi.

Vapi sends a credential we configure in its dashboard
(docs.vapi.ai/server-url/server-authentication, read 2026-11-03):
- a Bearer credential arrives as ``Authorization: Bearer <secret>``;
- the legacy form arrives as ``X-Vapi-Secret: <secret>``;
- the Custom LLM API key arrives in the ``Authorization`` header.

All three are accepted and compared in constant time against ``VAPI_WEBHOOK_SECRET``. Without
a configured secret every request is refused: an unauthenticated voice endpoint would let
anyone run the agent or inject call reports.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.core.settings import Settings


def presented_secrets(request: Request) -> list[str]:
    out: list[str] = []
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    if auth:
        out.append(auth)
    legacy = (request.headers.get("x-vapi-secret") or "").strip()
    if legacy:
        out.append(legacy)
    return out


def verify_vapi_request(request: Request, settings: Settings) -> None:
    expected = settings.vapi_webhook_secret
    if expected is None or not expected.get_secret_value():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Vapi is not configured")
    want = expected.get_secret_value().encode()
    if not any(hmac.compare_digest(got.encode(), want) for got in presented_secrets(request)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing Vapi secret")
