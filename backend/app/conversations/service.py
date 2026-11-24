"""Conversation and message storage.

Every message carries its channel and language. The language of inbound messages is detected
by the backend (``app.conversations.language``); a change from the previous inbound language is
recorded on the message (``language_switched``), and the conversation language and the
customer's ``preferred_language`` follow the customer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.conversations.language import choose_language
from app.core.config import ClientConfig
from app.core.enums import Channel, MessageDirection, MessageSender
from app.db.models import Conversation, Customer, Message

# A new conversation starts after this much silence on the same channel.
CONVERSATION_IDLE_HOURS = 12


async def get_or_start(
    db: AsyncSession,
    *,
    customer: Customer,
    channel: Channel,
    config: ClientConfig,
    now: datetime,
) -> Conversation:
    """The customer's open conversation on this channel, or a new one."""
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.customer_id == customer.id,
            Conversation.channel == channel,
            Conversation.ended_at.is_(None),
        )
        .order_by(Conversation.started_at.desc())
        .limit(1)
    )
    current = result.scalar_one_or_none()
    if current is not None:
        last = current.last_message_at or current.started_at
        if now - last <= timedelta(hours=CONVERSATION_IDLE_HOURS):
            return current
        current.ended_at = last
    language = (
        customer.preferred_language
        if customer.preferred_language in config.agent.languages
        else config.agent.default_language
    )
    conversation = Conversation(
        customer_id=customer.id, channel=channel, language=language, started_at=now
    )
    db.add(conversation)
    await db.flush()
    return conversation


@dataclass(frozen=True)
class Recorded:
    message: Message
    language: str
    switched: bool


async def _last_inbound_language(db: AsyncSession, conversation: Conversation) -> str | None:
    result = await db.execute(
        select(Message.language)
        .where(
            Message.conversation_id == conversation.id,
            Message.direction == MessageDirection.INBOUND,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def record_inbound(
    db: AsyncSession,
    *,
    conversation: Conversation,
    customer: Customer,
    text: str,
    config: ClientConfig,
    now: datetime,
    provider_message_id: str | None = None,
) -> Recorded:
    previous = await _last_inbound_language(db, conversation)
    current = previous or conversation.language
    language, switched = choose_language(
        text,
        current=current,
        languages=list(config.agent.languages),
        default_language=config.agent.default_language,
    )
    message = Message(
        conversation_id=conversation.id,
        channel=conversation.channel,
        direction=MessageDirection.INBOUND,
        sender=MessageSender.CUSTOMER,
        language=language,
        language_switched=switched,
        content=text,
        provider_message_id=provider_message_id,
        created_at=now,
    )
    db.add(message)
    conversation.language = language
    conversation.last_message_at = now
    customer.preferred_language = language
    customer.last_contact_at = now
    await db.flush()
    return Recorded(message, language, switched)


async def record_outbound(
    db: AsyncSession,
    *,
    conversation: Conversation,
    text: str,
    language: str,
    now: datetime,
    sender: MessageSender = MessageSender.AGENT,
    tool_calls: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        channel=conversation.channel,
        direction=MessageDirection.OUTBOUND,
        sender=sender,
        language=language,
        content=text,
        tool_calls=tool_calls,
        meta=meta or {},
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    await db.flush()
    return message


async def history(db: AsyncSession, conversation: Conversation, limit: int = 30) -> list[Message]:
    """Last ``limit`` messages, oldest first. At equal timestamps the inbound message is first."""
    outbound_last = case((Message.direction == MessageDirection.INBOUND, 0), else_=1)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc(), outbound_last.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
