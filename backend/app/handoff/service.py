"""Human handoff records with full context (brief §8, F12).

A handoff row tells the sales team a person must act on a lead, and carries everything they
need without reading the whole conversation: requirements, recent messages, matched properties,
qualification, objections raised and a recommended next action. Stage 3 creates the rows (on a
visit request, a transfer, an escalation, an AI failure or the kill switch). Notifying the
salesperson, acknowledging and the dashboard queue are Stage 5.

Context text is for staff (English); nothing here is shown to customers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig, SalesPerson
from app.core.enums import HandoffStatus, VisitState
from app.customers import memory
from app.db.models import (
    Conversation,
    Customer,
    CustomerIdentity,
    Handoff,
    Lead,
    LeadMatch,
    Message,
    Objection,
    Project,
    SiteVisit,
    User,
)
from app.leads import requirements as reqs

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
RESPONSE_MINUTES = 15  # due time for a handoff raised during working hours
RECENT_MESSAGES = 12


def in_working_hours(config: ClientConfig, now: datetime) -> bool:
    wh = config.escalation.working_hours
    local = now.astimezone(ZoneInfo(config.company.timezone))
    start, end = (datetime.strptime(v, "%H:%M").time() for v in (wh.start, wh.end))
    return WEEKDAYS[local.weekday()] in wh.days and start <= local.time() < end


def next_working_start(config: ClientConfig, now: datetime) -> datetime:
    wh = config.escalation.working_hours
    tz = ZoneInfo(config.company.timezone)
    local = now.astimezone(tz)
    start = datetime.strptime(wh.start, "%H:%M").time()
    for offset in range(0, 8):
        day = local.date() + timedelta(days=offset)
        candidate = datetime.combine(day, start, tz)
        if WEEKDAYS[day.weekday()] in wh.days and candidate > local:
            return candidate
    return local + timedelta(days=1)  # unreachable with a valid config


def pick_salesperson(config: ClientConfig, language: str) -> SalesPerson:
    """The first sales team member who speaks the customer's language (else the first)."""
    for person in config.sales_team:
        if language in person.languages:
            return person
    return config.sales_team[0]


def _next_action(reason: str, missing: list[str], visit: SiteVisit | None,
                 project: str | None, after_hours: bool) -> str:  # fmt: skip
    when = "at the start of working hours" if after_hours else "now"
    if visit is not None and visit.state == VisitState.REQUESTED:
        return (
            f"Confirm or reschedule the requested site visit to {project} on "
            f"{visit.visit_date.isoformat()} {visit.slot.strftime('%H:%M')}, then call the "
            "customer."
        )
    if reason == "callback_requested":
        return f"Call the customer back {when} on the number shown above; they asked for a call."
    if reason == "human_requested":
        return f"Call the customer back {when}; they asked to speak to a person."
    if reason == "legal":
        return f"Have the right person answer the customer's legal question {when}. The AI did not."
    if reason == "angry":
        return f"Call the customer {when} and resolve the complaint."
    if reason in {"ai_failure", "unsafe_reply", "kill_switch", "usage_limit"}:
        return f"Reply to the customer {when}; the AI could not answer ({reason})."
    if missing:
        return "Call the customer and ask about: " + ", ".join(missing) + "."
    return "Call the customer and offer a site visit to the matched projects."


async def build_context(
    db: AsyncSession,
    config: ClientConfig,
    *,
    lead: Lead,
    customer: Customer,
    conversation: Conversation | None,
    reason: str,
    now: datetime,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req = await reqs.load(db, lead)
    missing = req.missing(list(config.lead_scoring.qualified_requires))
    identities = (
        await db.execute(
            select(
                CustomerIdentity.identity_type, CustomerIdentity.value, CustomerIdentity.verified
            ).where(CustomerIdentity.customer_id == customer.id)
        )
    ).tuples().all()  # fmt: skip
    messages: list[dict[str, Any]] = []
    if conversation is not None:
        rows = (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc())
                .limit(RECENT_MESSAGES)
            )
        ).scalars().all()  # fmt: skip
        messages = [
            {
                "from": m.sender.value,
                "language": m.language,
                "text": m.content,
                "at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in reversed(rows)
        ]
    latest = await db.scalar(select(LeadMatch.created_at).where(LeadMatch.lead_id == lead.id)
                             .order_by(LeadMatch.created_at.desc()).limit(1))  # fmt: skip
    matches: list[dict[str, Any]] = []
    if latest is not None:
        rows2 = await db.execute(
            select(LeadMatch, Project.code, Project.name)
            .join(Project, Project.id == LeadMatch.project_id)
            .where(LeadMatch.lead_id == lead.id, LeadMatch.created_at == latest)
            .order_by(LeadMatch.rank)
        )
        matches = [
            {"rank": m.rank, "project_code": code, "project": name,
             "match_score": float(m.match_score), "reason": m.reasons.get("en")}
            for m, code, name in rows2.tuples()
        ]  # fmt: skip
    objections = (
        await db.execute(
            select(Objection.objection_type, Objection.text, Objection.language)
            .where(Objection.lead_id == lead.id)
            .order_by(Objection.created_at)
        )
    ).tuples().all()  # fmt: skip
    visit = await db.scalar(
        select(SiteVisit).where(SiteVisit.lead_id == lead.id).order_by(SiteVisit.created_at.desc())
    )
    project_name = None
    if visit is not None:
        project_name = await db.scalar(select(Project.name).where(Project.id == visit.project_id))
    after_hours = not in_working_hours(config, now)
    return {
        "reason": reason,
        "details": details or {},
        "customer": {
            "name": customer.name,
            "preferred_language": customer.preferred_language,
            "identities": [
                {"type": t.value, "value": v, "verified": ok} for t, v, ok in identities
            ],
            "memory": memory.summary(customer),
        },
        "lead": {"state": lead.state.value, "score": lead.score,
                 "band": lead.band.value if lead.band else None, "score_reason": lead.score_reason},
        "requirements": req.model_dump(),
        "missing_requirements": missing,
        "matches": matches,
        "objections": [{"type": t, "text": x, "language": lang} for t, x, lang in objections],
        "visit": None if visit is None else {
            "project": project_name, "date": visit.visit_date.isoformat(),
            "slot": visit.slot.strftime("%H:%M"), "state": visit.state.value,
            "visitor_name": visit.visitor_name,
        },
        "recent_messages": messages,
        "after_hours": after_hours,
        "recommended_next_action": _next_action(reason, missing, visit, project_name, after_hours)
        + (f" They prefer: {prefer}." if (prefer := (details or {}).get("preferred_time")) else ""),
    }  # fmt: skip


async def create_handoff(
    db: AsyncSession,
    config: ClientConfig,
    *,
    lead: Lead,
    customer: Customer,
    conversation: Conversation | None,
    reason: str,
    now: datetime,
    details: dict[str, Any] | None = None,
) -> Handoff:
    """Open a handoff (or refresh the open one with the same reason). Flushes."""
    context = await build_context(
        db, config, lead=lead, customer=customer, conversation=conversation, reason=reason,
        now=now, details=details,
    )  # fmt: skip
    existing = await db.scalar(
        select(Handoff).where(
            Handoff.lead_id == lead.id, Handoff.reason == reason,
            Handoff.status == HandoffStatus.OPEN,
        )
    )  # fmt: skip
    if existing is not None:
        existing.context = context
        await db.flush()
        return existing
    language = (conversation.language if conversation else None) or (
        customer.preferred_language or config.agent.default_language
    )
    person = pick_salesperson(config, language)
    user_id: uuid.UUID | None = await db.scalar(
        select(User.id).where(User.email == person.email.lower(), User.is_active.is_(True))
    )
    due = (
        now + timedelta(minutes=RESPONSE_MINUTES)
        if in_working_hours(config, now)
        else next_working_start(config, now)
    )
    handoff = Handoff(
        lead_id=lead.id,
        assigned_email=person.email,
        assigned_user_id=user_id,
        reason=reason,
        status=HandoffStatus.OPEN,
        context=context,
        due_at=due,
    )
    db.add(handoff)
    await db.flush()
    return handoff
