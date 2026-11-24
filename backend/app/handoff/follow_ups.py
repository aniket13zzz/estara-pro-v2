"""Follow-up scheduling (brief §8: follow-ups for leads who did not book).

Stage 3 creates the ``follow_ups`` row when the customer asks to be contacted later; Stage 5's
worker sends it on the configured channel with an approved template, in the customer's language.
Follow-ups go only on channels in ``follow_up.channels`` (WhatsApp while outbound calls are off)
and never to a customer who opted out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import WHATSAPP_TEMPLATE_DIR, ClientConfig
from app.core.enums import Channel, FollowUpStatus, LeadState
from app.db.models import Customer, FollowUp, Lead
from app.leads.state import Actor, transition

MAX_DAYS_AHEAD = 60
NURTURE_FROM = (LeadState.ENGAGED, LeadState.QUALIFIED)


@dataclass(frozen=True)
class Scheduled:
    follow_up: FollowUp | None
    status: str  # scheduled | rescheduled | opted_out | channel_not_allowed


def follow_up_template(config: ClientConfig, language: str) -> str | None:
    folder = config.scripts_root / language / WHATSAPP_TEMPLATE_DIR
    names = sorted(p.stem for p in folder.glob("*.md"))
    preferred = [n for n in names if "follow_up" in n] or names
    return preferred[0] if preferred else None


async def schedule_follow_up(
    db: AsyncSession,
    config: ClientConfig,
    *,
    lead: Lead,
    customer: Customer,
    language: str,
    now: datetime,
    days: int | None = None,
    channel: str | None = None,
) -> Scheduled:
    allowed = list(config.follow_up.channels)
    chosen = channel or allowed[0]
    if chosen not in allowed:
        return Scheduled(None, "channel_not_allowed")
    if chosen == "whatsapp" and customer.whatsapp_opted_out:
        return Scheduled(None, "opted_out")
    wait = days if days is not None else config.follow_up.schedule_days[0]
    wait = max(1, min(int(wait), MAX_DAYS_AHEAD))
    # One waiting follow-up per lead: asking again moves it to the new time instead of adding a
    # second message. Locking the lead row makes two requests at the same moment queue up.
    await db.execute(select(Lead.id).where(Lead.id == lead.id).with_for_update())
    waiting = await db.scalar(
        select(FollowUp).where(FollowUp.lead_id == lead.id,
                               FollowUp.status == FollowUpStatus.SCHEDULED)
        .order_by(FollowUp.scheduled_for)
    )  # fmt: skip
    if waiting is not None:
        waiting.channel = Channel(chosen)
        waiting.language = language
        whatsapp = chosen == "whatsapp"
        waiting.template_name = follow_up_template(config, language) if whatsapp else None
        waiting.scheduled_for = now + timedelta(days=wait)
        await db.flush()
        return Scheduled(waiting, "rescheduled")
    follow_up = FollowUp(
        lead_id=lead.id,
        customer_id=customer.id,
        channel=Channel(chosen),
        language=language,
        template_name=follow_up_template(config, language) if chosen == "whatsapp" else None,
        scheduled_for=now + timedelta(days=wait),
        status=FollowUpStatus.SCHEDULED,
    )
    db.add(follow_up)
    if lead.state in NURTURE_FROM:
        await transition(db, lead, LeadState.NURTURING, actor=Actor.SYSTEM,
                         trigger="customer asked to be contacted later")  # fmt: skip
    await db.flush()
    return Scheduled(follow_up, "scheduled")
