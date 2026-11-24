"""WhatsApp webhook (Meta Cloud API), a public path protected by Meta's signature.

- ``GET /api/v1/webhooks/whatsapp``: Meta's verification (``hub.mode=subscribe``,
  ``hub.verify_token`` must equal ``WHATSAPP_VERIFY_TOKEN``; reply with ``hub.challenge``).
- ``POST /api/v1/webhooks/whatsapp``: messages and delivery statuses. The signature
  (``X-Hub-Signature-256``) is checked against the exact body bytes with ``WHATSAPP_APP_SECRET``.
  Meta wants a quick 200 and retries for up to 7 days otherwise, so statuses are applied at once
  and each message is answered after the response; a redelivered message is ignored.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.brain.scripts import Scripts
from app.brain.service import SalesBrain
from app.channels.whatsapp import service
from app.conversations.service import record_inbound, record_outbound
from app.core.config import ClientConfig
from app.core.settings import Settings
from app.providers.embeddings import lazy_embedder
from app.providers.llm import LLMClient, LLMNotConfigured, get_llm
from app.providers.whatsapp import (
    WhatsAppClient,
    WhatsAppError,
    WhatsAppNotConfigured,
    client_from,
    valid_signature,
)
from app.truth.staleness import Clock, SystemClock

log = logging.getLogger("estara.whatsapp")
router = APIRouter(prefix="/api/v1/webhooks/whatsapp", tags=["whatsapp"])


def _config(app: Any) -> ClientConfig:
    cfg: ClientConfig = app.state.client_config
    return cfg


def _settings(app: Any) -> Settings:
    s: Settings = app.state.settings
    return s


def _clock(app: Any) -> Clock:
    clock: Clock = getattr(app.state, "clock", None) or SystemClock()
    return clock


def _llm(app: Any) -> LLMClient | None:
    llm: LLMClient | None = getattr(app.state, "llm", None)
    if llm is not None:
        return llm
    try:
        return get_llm(_config(app), _settings(app))
    except LLMNotConfigured as exc:
        log.warning("WhatsApp message without an LLM: %s", exc)
        return None


def whatsapp_client(app: Any) -> WhatsAppClient:
    client: WhatsAppClient | None = getattr(app.state, "whatsapp_client", None)
    return client or client_from(_settings(app))


@router.get("", response_class=PlainTextResponse)
async def verify(
    request: Request,
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
) -> str:
    expected = _settings(request.app).whatsapp_verify_token
    ok = (
        mode == "subscribe"
        and expected is not None
        and token is not None
        and hmac.compare_digest(token.encode(), expected.get_secret_value().encode())
    )
    if not ok or challenge is None:
        raise HTTPException(403, "Verification failed")
    return challenge


@router.post("")
async def receive(request: Request, background: BackgroundTasks) -> JSONResponse:
    settings = _settings(request.app)
    if settings.whatsapp_app_secret is None:
        raise HTTPException(503, "WhatsApp is not configured")
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")
    if not valid_signature(settings.whatsapp_app_secret.get_secret_value(), body, signature):
        raise HTTPException(401, "Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(400, "Body must be JSON") from None
    messages, statuses = service.parse_webhook(payload if isinstance(payload, dict) else {})
    if statuses:
        async with request.app.state.sessionmaker() as db:
            for status in statuses:
                await service.apply_status(db, status)
            await db.commit()
    for message in messages:
        background.add_task(handle_message, request.app, message)
    return JSONResponse({"ok": True})


async def handle_message(app: Any, message: dict[str, Any]) -> None:
    """Answer one customer message. Never raises: a failure is logged and the message stays
    recorded, so staff see it on the lead."""
    try:
        await _handle(app, message)
    except Exception:
        log.exception("WhatsApp message %s could not be answered", message.get("id"))


async def _handle(app: Any, message: dict[str, Any]) -> None:
    config, now = _config(app), _clock(app).now()
    provider_id = str(message["id"])[:200]
    async with app.state.sessionmaker() as db:
        if await service.already_seen(db, provider_id):
            return
        customer = await service.find_or_create_sender(
            db, config, str(message["from"]), message.get("_profile_name"), now
        )
        text = service.text_of(message)
        if text is not None and service.is_opt_out(config, text):
            await service.record_opt_out(db, config, customer, text, provider_id, now)
            await db.commit()
            return
        # Writing to us is consent to replies and visit notices (a new message after an
        # opt-out is a new consent).
        customer.whatsapp_opt_in_at = customer.whatsapp_opt_in_at or now
        customer.whatsapp_opted_out = False
        conversation = await service.whatsapp_conversation(db, config, customer, now)
        to = await service.phone_of(db, customer)
        if text is None:  # voice note, photo, file: ask for text
            await record_inbound(
                db, conversation=conversation, customer=customer,
                text=f"[{message.get('type', 'unsupported')} message]", config=config, now=now,
                provider_message_id=provider_id,
            )  # fmt: skip
            language = service.language_of(config, customer)
            reply_text = Scripts(config).text(language, "whatsapp_unsupported.md")
            reply_message = await record_outbound(
                db, conversation=conversation, text=reply_text, language=language, now=now
            )
        else:
            brain = SalesBrain(
                db,
                config,
                llm=_llm(app),
                clock=_clock(app),
                embedder=lazy_embedder(app.state, config, _settings(app)),
            )
            reply = await brain.respond(conversation, text, provider_message_id=provider_id)
            reply_text, reply_message = reply.text, reply.message
        await db.commit()
        if not reply_text or to is None:
            return
        try:
            reply_message.provider_message_id = await whatsapp_client(app).send_text(to, reply_text)
            reply_message.delivery_status = "sent"
        except (WhatsAppError, WhatsAppNotConfigured) as exc:
            log.warning("WhatsApp reply not sent: %s", exc)
            reply_message.delivery_status = "failed"
        await db.commit()
