"""WhatsApp notices, sent by the worker every minute, each at most once:

- ``visit_requested``: after the agent requests a visit (any channel);
- ``visit_confirmation``: after staff confirm (or reschedule) it;
- ``visit_reminder``: the day before a confirmed visit, from 09:00 in the client's timezone;
- the follow-up template, for follow-ups that are due.

A notice goes only to a customer who agreed to WhatsApp and did not opt out, and who has a
known number. Otherwise it is marked handled without sending (logged), so it is never sent
late by surprise. A temporary failure (WhatsApp down, rate limit) is retried next minute; a
permanent one is marked handled and logged.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.scripts import Scripts
from app.channels.whatsapp.service import send_template
from app.core.config import ClientConfig
from app.core.enums import Channel, FollowUpStatus, VisitState
from app.db.models import Customer, FollowUp, Handoff, Project, SiteVisit
from app.providers.whatsapp import WhatsAppClient, WhatsAppError
from app.truth.formatters import ENGLISH_MONTHS, HINDI_MONTHS, formatter_for

log = logging.getLogger("estara.whatsapp")
REQUESTED_WITHIN = timedelta(days=2)  # never announce an old request (e.g. after downtime)
REMINDER_FROM = time(9, 0)
MONTHS = {"en": ENGLISH_MONTHS, "hi": HINDI_MONTHS}


def visit_date(language: str, d: Any) -> str:
    months = MONTHS.get(language, ENGLISH_MONTHS)
    return f"{formatter_for(language).weekday(d)} {d.day} {months[d.month - 1]}"


async def salesperson(db: AsyncSession, config: ClientConfig, visit: SiteVisit) -> str:
    email = await db.scalar(
        select(Handoff.assigned_email).where(Handoff.lead_id == visit.lead_id,
                                             Handoff.assigned_email.is_not(None))
        .order_by(Handoff.created_at.desc()).limit(1)
    )  # fmt: skip
    for person in config.sales_team:
        if person.email == email:
            return person.name
    return config.sales_team[0].name


def customer_name(config: ClientConfig, customer: Customer, language: str,
                  fallback: str | None = None) -> str:  # fmt: skip
    name = (fallback or customer.name or "").strip()
    return name or Scripts(config).text(language, "customer_name_fallback.md")


async def visit_values(db: AsyncSession, config: ClientConfig, visit: SiteVisit,
                       customer: Customer) -> dict[str, str]:  # fmt: skip
    lang = customer.preferred_language
    language = lang if lang in config.agent.languages else config.agent.default_language
    project = await db.get(Project, visit.project_id)
    return {
        "customer_name": customer_name(config, customer, language, visit.visitor_name),
        "project_name": project.name if project else "",
        "visit_date": visit_date(language, visit.visit_date),
        "visit_time": formatter_for(language).time_of_day(visit.slot),
        "salesperson_name": await salesperson(db, config, visit),
    }


Sender = Callable[[], Awaitable[Any]]


async def _attempt(label: str, send: Sender) -> str:
    """``sent``, ``skipped`` (not allowed, or refused for good: handled, never retried) or
    ``retry`` (a temporary failure: try again next minute)."""
    try:
        result = await send()
    except WhatsAppError as exc:
        if exc.retryable:
            log.warning("%s: temporary WhatsApp failure, will retry: %s", label, exc)
            return "retry"
        log.error("%s: WhatsApp refused the message: %s", label, exc)
        return "skipped"
    if result is None:
        log.info("%s: not sent (no WhatsApp consent, opted out, or no number)", label)
        return "skipped"
    return "sent"


async def send_due_notices(db: AsyncSession, config: ClientConfig, client: WhatsAppClient,
                           now: datetime) -> dict[str, int]:  # fmt: skip
    counts = {"requested": 0, "confirmed": 0, "reminder": 0, "follow_up": 0}
    tz = ZoneInfo(config.company.timezone)
    local = now.astimezone(tz)

    async def visit_notice(visit: SiteVisit, name: str, field: str, key: str) -> None:
        customer = await db.get(Customer, visit.customer_id)
        if customer is None:
            return
        values = await visit_values(db, config, visit, customer)

        async def send() -> Any:
            return await send_template(db, config, client, customer, name=name, values=values,
                                       now=now)  # fmt: skip

        outcome = await _attempt(f"{name} for visit {visit.id}", send)
        if outcome != "retry":
            setattr(visit, field, now)
        if outcome == "sent":
            counts[key] += 1
        await db.commit()

    requested = (await db.execute(
        select(SiteVisit).where(SiteVisit.requested_notice_at.is_(None),
                                SiteVisit.state == VisitState.REQUESTED,
                                SiteVisit.created_at >= now - REQUESTED_WITHIN)
    )).scalars().all()  # fmt: skip
    for visit in requested:
        await visit_notice(visit, "visit_requested", "requested_notice_at", "requested")

    confirmed = (await db.execute(
        select(SiteVisit).where(SiteVisit.confirmed_notice_at.is_(None),
                                SiteVisit.state == VisitState.CONFIRMED,
                                SiteVisit.visit_date >= local.date())
    )).scalars().all()  # fmt: skip
    for visit in confirmed:
        await visit_notice(visit, "visit_confirmation", "confirmed_notice_at", "confirmed")

    if local.time() >= REMINDER_FROM:
        tomorrow = local.date() + timedelta(days=1)
        due = (await db.execute(
            select(SiteVisit).where(SiteVisit.reminder_sent_at.is_(None),
                                    SiteVisit.state == VisitState.CONFIRMED,
                                    SiteVisit.visit_date == tomorrow)
        )).scalars().all()  # fmt: skip
        for visit in due:
            await visit_notice(visit, "visit_reminder", "reminder_sent_at", "reminder")

    follow_ups = (await db.execute(
        select(FollowUp).where(FollowUp.status == FollowUpStatus.SCHEDULED,
                               FollowUp.channel == Channel.WHATSAPP,
                               FollowUp.scheduled_for <= now)
    )).scalars().all()  # fmt: skip
    for follow_up in follow_ups:
        customer = await db.get(Customer, follow_up.customer_id)
        if customer is None or not follow_up.template_name:
            follow_up.status = FollowUpStatus.SKIPPED
            await db.commit()
            continue
        follow_up.attempt += 1
        try:
            message = await send_template(
                db, config, client, customer, name=follow_up.template_name, now=now,
                values={"customer_name": customer_name(config, customer, follow_up.language)},
            )  # fmt: skip
        except WhatsAppError as exc:
            log.warning("follow-up %s: WhatsApp failure: %s", follow_up.id, exc)
            if not exc.retryable:
                follow_up.status, follow_up.error = FollowUpStatus.FAILED, str(exc)[:500]
            await db.commit()
            continue
        if message is None:
            log.info("follow-up %s: not sent (no consent, opted out, or no number)", follow_up.id)
            follow_up.status = FollowUpStatus.SKIPPED
        else:
            follow_up.status, follow_up.sent_at = FollowUpStatus.SENT, now
            follow_up.provider_message_id = message.provider_message_id
            counts["follow_up"] += 1
        await db.commit()
    return counts
