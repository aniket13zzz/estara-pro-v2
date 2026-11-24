"""WhatsApp: inbound messages through the Sales Brain, and once-only visit notices.

Rules (CLAUDE.md, phase-plan.md "Validation rules"):
- A message is answered once: its WhatsApp message id is stored and a redelivery is ignored.
- The sender is identified only by the WhatsApp number that sent it (a verified identity), so
  it joins the same customer record as a call from that number, and nothing a sender writes can
  point the agent at someone else.
- Opt-out: only when the whole message (trimmed, ignoring case and end punctuation) equals a
  keyword from ``channels.whatsapp.opt_out_keywords``. No further messages are sent.
- Messaging us is consent to replies and visit notices; a later normal message after an opt-out
  is a new consent. On calls the agent asks before WhatsApp is used.
- Inside the 24-hour window (the customer wrote to us in the last 24 hours) plain text is sent;
  outside it only approved templates (``client/scripts/<lang>/whatsapp/<name>.md``).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.scripts import COMMENT
from app.conversations.service import get_or_start, record_inbound, record_outbound
from app.core.config import WHATSAPP_TEMPLATE_DIR, ClientConfig
from app.core.enums import Channel, IdentityType, MessageDirection
from app.customers.identity import IdentityClaim, country_code_of, resolve
from app.db.models import Conversation, Customer, CustomerIdentity, Message
from app.providers.whatsapp import WhatsAppClient

log = logging.getLogger("estara.whatsapp")
WINDOW = timedelta(hours=24)
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
END_PUNCTUATION = ".!?।,"


# ------------------------------------------------------------------------------ helpers


def is_opt_out(config: ClientConfig, text: str) -> bool:
    cleaned = text.strip().strip(END_PUNCTUATION).strip().casefold()
    keywords = config.channels.whatsapp.opt_out_keywords
    return any(cleaned == k.strip().casefold() for words in keywords.values() for k in words)


def language_of(config: ClientConfig, customer: Customer) -> str:
    lang = customer.preferred_language
    return lang if lang in config.agent.languages else config.agent.default_language


async def phone_of(db: AsyncSession, customer: Customer) -> str | None:
    """The customer's verified WhatsApp number, else verified phone (a caller's number)."""
    rows = (await db.execute(
        select(CustomerIdentity.identity_type, CustomerIdentity.value)
        .where(CustomerIdentity.customer_id == customer.id, CustomerIdentity.verified.is_(True),
               CustomerIdentity.identity_type.in_([IdentityType.WHATSAPP, IdentityType.PHONE]))
    )).tuples().all()  # fmt: skip
    by_type = {t: v for t, v in rows}
    return by_type.get(IdentityType.WHATSAPP) or by_type.get(IdentityType.PHONE)


async def window_open(db: AsyncSession, customer: Customer, now: datetime) -> bool:
    last = await db.scalar(
        select(Message.created_at)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Conversation.customer_id == customer.id, Message.channel == Channel.WHATSAPP,
               Message.direction == MessageDirection.INBOUND)
        .order_by(Message.created_at.desc()).limit(1)
    )  # fmt: skip
    return last is not None and now - last < WINDOW


def may_message(customer: Customer) -> bool:
    return customer.whatsapp_opt_in_at is not None and not customer.whatsapp_opted_out


# -------------------------------------------------------------------------- templates


def template_raw(config: ClientConfig, language: str, name: str) -> str:
    """The template file with its {{placeholders}} kept (reviewer comments removed)."""
    path = config.scripts_root / language / WHATSAPP_TEMPLATE_DIR / f"{name}.md"
    return COMMENT.sub("", path.read_text(encoding="utf-8")).strip()


def template_keys(config: ClientConfig, language: str, name: str) -> list[str]:
    """Placeholders in order of first appearance: Meta's {{1}}, {{2}}, ... in that order."""
    seen: list[str] = []
    for key in PLACEHOLDER.findall(template_raw(config, language, name)):
        if key not in seen:
            seen.append(key)
    return seen


def meta_template_text(config: ClientConfig, language: str, name: str) -> str:
    """The template body to submit to Meta for approval, with numbered parameters."""
    keys = template_keys(config, language, name)
    return PLACEHOLDER.sub(lambda m: f"{{{{{keys.index(m.group(1)) + 1}}}}}",
                           template_raw(config, language, name))  # fmt: skip


def render_template(config: ClientConfig, language: str, name: str,
                    values: dict[str, str]) -> tuple[str, list[str]]:  # fmt: skip
    """The filled-in text (for plain sends and the timeline) and Meta's ordered parameters."""
    filled = {"company_name": config.company.name, "agent_name": config.agent.name, **values}
    missing = [k for k in template_keys(config, language, name) if k not in filled]
    if missing:
        raise ValueError(f"template {name!r} needs values for {missing}")
    keys = template_keys(config, language, name)
    text = PLACEHOLDER.sub(lambda m: filled[m.group(1)], template_raw(config, language, name))
    return text, [filled[k] for k in keys]


# ------------------------------------------------------------------------------ sending


async def whatsapp_conversation(db: AsyncSession, config: ClientConfig, customer: Customer,
                                now: datetime) -> Conversation:  # fmt: skip
    return await get_or_start(db, customer=customer, channel=Channel.WHATSAPP, config=config,
                              now=now)  # fmt: skip


async def send_template(
    db: AsyncSession, config: ClientConfig, client: WhatsAppClient, customer: Customer, *,
    name: str, values: dict[str, str], now: datetime,
) -> Message | None:  # fmt: skip
    """A business-initiated notice: needs consent, no opt-out and a known number. Inside the
    24-hour window it goes as plain text, otherwise as the approved template. Raises
    ``WhatsAppError`` on a send failure (the caller decides about retrying)."""
    if not may_message(customer):
        return None
    to = await phone_of(db, customer)
    if to is None:
        return None
    language = language_of(config, customer)
    text, parameters = render_template(config, language, name, values)
    if await window_open(db, customer, now):
        provider_id = await client.send_text(to, text)
    else:
        provider_id = await client.send_template(to, name, language, parameters)
    conversation = await whatsapp_conversation(db, config, customer, now)
    message = await record_outbound(db, conversation=conversation, text=text, language=language,
                                    now=now, meta={"template": name})  # fmt: skip
    message.provider_message_id, message.delivery_status = provider_id, "sent"
    await db.flush()
    return message


# ------------------------------------------------------------------------------ inbound


async def find_or_create_sender(db: AsyncSession, config: ClientConfig, wa_id: str,
                                profile_name: str | None, now: datetime) -> Customer:  # fmt: skip
    default_cc = country_code_of(config.escalation.human_transfer_number)
    resolution = await resolve(
        db, [IdentityClaim(IdentityType.WHATSAPP, wa_id, verified=True)],
        primary=IdentityType.WHATSAPP, default_country_code=default_cc, now=now,
    )  # fmt: skip
    customer = resolution.customer
    if profile_name and not customer.name:
        customer.name = profile_name.strip()[:200] or None
    return customer


async def already_seen(db: AsyncSession, provider_id: str) -> bool:
    found = await db.scalar(select(Message.id).where(Message.provider_message_id == provider_id))
    return found is not None


async def record_opt_out(db: AsyncSession, config: ClientConfig, customer: Customer, text: str,
                         provider_id: str, now: datetime) -> None:  # fmt: skip
    customer.whatsapp_opted_out, customer.whatsapp_opted_out_at = True, now
    conversation = await whatsapp_conversation(db, config, customer, now)
    recorded = await record_inbound(
        db, conversation=conversation, customer=customer, text=text, config=config, now=now,
        provider_message_id=provider_id,
    )  # fmt: skip
    recorded.message.meta = {"opt_out": True}
    await db.flush()


def parse_webhook(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Messages and statuses from Meta's ``entry[].changes[].value``."""
    messages: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            value = (change or {}).get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name")
                     for c in value.get("contacts") or [] if isinstance(c, dict)}  # fmt: skip
            for m in value.get("messages") or []:
                if isinstance(m, dict) and m.get("id") and m.get("from"):
                    messages.append({**m, "_profile_name": names.get(m["from"])})
            statuses += [s for s in value.get("statuses") or [] if isinstance(s, dict)]
    return messages, statuses


def text_of(message: dict[str, Any]) -> str | None:
    kind = message.get("type")
    if kind == "text":
        body = (message.get("text") or {}).get("body")
        return body if isinstance(body, str) else None
    if kind == "button":  # a quick-reply button on a template
        body = (message.get("button") or {}).get("text")
        return body if isinstance(body, str) else None
    if kind == "interactive":
        reply = message.get("interactive") or {}
        for key in ("button_reply", "list_reply"):
            title = (reply.get(key) or {}).get("title")
            if isinstance(title, str):
                return title
    return None


async def apply_status(db: AsyncSession, status: dict[str, Any]) -> None:
    provider_id, state = status.get("id"), status.get("status")
    if not isinstance(provider_id, str) or not isinstance(state, str):
        return
    message = await db.scalar(select(Message).where(Message.provider_message_id == provider_id))
    if message is not None:
        message.delivery_status = state[:32]
        if state == "failed":
            message.meta = {**(message.meta or {}), "errors": status.get("errors")}
