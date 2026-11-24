"""Calls: one ``calls`` row per Vapi call, linked to its customer and voice conversation.

- The caller is identified only by the call itself: the caller ID of a phone call (a verified
  phone identity, ADR 0010) or, for a browser test call, a new customer. Nothing the caller
  *says* can point the agent at another customer.
- Each caller turn is stored with ``provider_message_id = vapi:<call id>:<turn number>``, so a
  turn Vapi sends twice (for example after the caller interrupts) is answered once and the
  stored reply is returned again.
- The end-of-call report fills in transcript, recording link, duration, ended reason and cost,
  plus a short English summary for staff. A report can arrive twice; it is stored once.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.service import get_or_start
from app.core.config import ClientConfig
from app.core.enums import Channel, IdentityType, MessageDirection, WebhookProvider
from app.customers.identity import IdentityClaim, country_code_of, resolve
from app.db.models import (
    Call,
    Conversation,
    Customer,
    Handoff,
    Lead,
    Message,
    SiteVisit,
    WebhookEvent,
)

log = logging.getLogger("estara.vapi")


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def message_id(vapi_call_id: str, turn: int) -> str:
    return f"vapi:{vapi_call_id}:{turn}"


def caller_number(body: dict[str, Any]) -> str | None:
    """The caller's number from a Vapi payload (``call.customer.number`` or ``customer.number``),
    present on phone calls and absent on browser test calls."""
    for source in (as_dict(body.get("call")).get("customer"), body.get("customer")):
        number = as_dict(source).get("number")
        if isinstance(number, str) and number.strip():
            return number.strip()
    return None


def call_id_of(body: dict[str, Any]) -> str | None:
    value = as_dict(body.get("call")).get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()[:200]
    return None


async def get_call(db: AsyncSession, vapi_call_id: str) -> Call | None:
    found: Call | None = await db.scalar(select(Call).where(Call.vapi_call_id == vapi_call_id))
    return found


async def start_or_get_call(
    db: AsyncSession,
    config: ClientConfig,
    *,
    vapi_call_id: str,
    number: str | None,
    now: datetime,
) -> tuple[Call, Customer, Conversation]:
    call = await get_call(db, vapi_call_id)
    if call is not None and call.conversation_id and call.customer_id:
        conversation = await db.get(Conversation, call.conversation_id)
        customer = await db.get(Customer, call.customer_id)
        if conversation is not None and customer is not None:
            return call, customer, conversation
    if number:
        default_cc = country_code_of(config.escalation.human_transfer_number)
        resolution = await resolve(
            db, [IdentityClaim(IdentityType.PHONE, number, verified=True)],
            primary=IdentityType.PHONE, default_country_code=default_cc, now=now,
        )  # fmt: skip
        customer = resolution.customer
    else:
        customer = Customer()
        db.add(customer)
        await db.flush()
    conversation = await get_or_start(db, customer=customer, channel=Channel.VOICE,
                                      config=config, now=now)  # fmt: skip
    if call is None:
        call = Call(vapi_call_id=vapi_call_id, started_at=now)
        db.add(call)
    call.customer_id = customer.id
    call.conversation_id = conversation.id
    call.caller_phone = number
    call.language = conversation.language
    await db.flush()
    return call, customer, conversation


async def stored_reply(
    db: AsyncSession, conversation: Conversation, provider_id: str
) -> str | None:
    """The reply already given to this exact turn, or None if the turn is new."""
    inbound = await db.scalar(select(Message).where(Message.provider_message_id == provider_id))
    if inbound is None:
        return None
    reply = await db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.created_at >= inbound.created_at,
        )
        .order_by(Message.created_at)
        .limit(1)
    )
    return reply or ""


# ------------------------------------------------------------------------- end of call


def _first_url(value: Any) -> str | None:
    """The first http(s) URL in a recording object. Vapi documents ``artifact.recording`` as an
    object with URLs without listing its fields, so every string value is checked."""
    if isinstance(value, str) and re.match(r"^https?://", value):
        return value
    if isinstance(value, dict):
        for key in sorted(value):
            found = _first_url(value[key])
            if found:
                return found
    return None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


async def outcome_of(db: AsyncSession, call: Call) -> str:
    if call.conversation_id is None:
        return "no_conversation"
    lead_ids = select(Lead.id).where(Lead.customer_id == call.customer_id)
    # The earlier of when we first saw the call and Vapi's reported start, so a clock
    # difference between Vapi and this server never hides a visit made during the call.
    seen = [t for t in (call.created_at, call.started_at) if t is not None]
    since = min(seen) if seen else None
    if since is None:
        return "enquiry"
    visit = await db.scalar(
        select(SiteVisit.id).where(SiteVisit.lead_id.in_(lead_ids),
                                   SiteVisit.created_at >= since).limit(1)
    )  # fmt: skip
    if visit is not None:
        return "visit_requested"
    handoff = await db.scalar(
        select(Handoff.id).where(Handoff.lead_id.in_(lead_ids),
                                 Handoff.created_at >= since).limit(1)
    )  # fmt: skip
    if handoff is not None:
        return "handed_off"
    return "enquiry"


async def already_processed(db: AsyncSession, event_id: str, event_type: str) -> bool:
    """Record a Vapi event id; True if it was seen before (duplicate delivery)."""
    exists = await db.scalar(
        select(WebhookEvent.id).where(WebhookEvent.provider == WebhookProvider.VAPI,
                                      WebhookEvent.event_id == event_id)
    )  # fmt: skip
    if exists is not None:
        return True
    db.add(WebhookEvent(provider=WebhookProvider.VAPI, event_id=event_id, event_type=event_type))
    await db.flush()
    return False


def transcript_of(message: dict[str, Any]) -> str | None:
    artifact = as_dict(message.get("artifact"))
    text = artifact.get("transcript")
    if isinstance(text, str) and text.strip():
        return text.strip()
    lines = []
    for item in artifact.get("messages") or []:
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            lines.append(f"{item.get('role', '?')}: {item['message']}")
    return "\n".join(lines) or None


async def record_end_of_call(
    db: AsyncSession, config: ClientConfig, message: dict[str, Any], *, now: datetime
) -> Call | None:
    vapi_call_id = call_id_of(message)
    if vapi_call_id is None:
        log.warning("end-of-call-report without call.id; keys: %s", sorted(message))
        return None
    if await already_processed(db, f"{vapi_call_id}:end-of-call-report", "end-of-call-report"):
        return await get_call(db, vapi_call_id)
    call = await get_call(db, vapi_call_id)
    if call is None:  # the caller hung up before saying anything
        call = Call(vapi_call_id=vapi_call_id, caller_phone=caller_number(message))
        db.add(call)
    info = as_dict(message.get("call"))
    artifact = as_dict(message.get("artifact"))
    call.started_at = _parse_time(info.get("startedAt")) or call.started_at
    call.ended_at = _parse_time(info.get("endedAt")) or now
    if call.started_at and call.ended_at:
        call.duration_seconds = max(0, int((call.ended_at - call.started_at).total_seconds()))
    call.ended_reason = str(message.get("endedReason") or info.get("endedReason") or "")[:100]
    call.recording_url = _first_url(artifact.get("recording")) or _first_url(
        {k: v for k, v in artifact.items() if "recording" in k.lower()}
    )
    call.transcript = transcript_of(message)
    call.cost_usd = _decimal(info.get("cost") if "cost" in info else message.get("cost"))
    call.outcome = await outcome_of(db, call)
    call.raw_report = {"type": "end-of-call-report", "keys": sorted(message),
                       "artifact_keys": sorted(artifact)}  # fmt: skip
    await db.flush()
    return call
