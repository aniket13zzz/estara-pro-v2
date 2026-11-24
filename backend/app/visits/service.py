"""Site-visit requests from the agent (brief §8: the AI may request, only staff confirm).

``request_visit`` checks the project and the slot against config and current bookings, creates
the visit in ``requested`` with its first event, moves the lead to ``visit_requested`` (ADR 0007,
system actor) and leaves confirmation to staff (Stage 5). Asking again for the same project
while a visit is pending returns the existing request instead of booking twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import Channel, LeadState, RecordStatus, VisitState
from app.db.models import Customer, Lead, Project, SiteVisit, SiteVisitEvent
from app.leads.state import Actor, transition
from app.visits.slots import HOLDING_STATES, available_slots, parse_slot_id, slot_id

MAX_SLOT_SCAN = 100


class VisitRequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # project_unavailable | slot_unavailable | lead_closed


@dataclass(frozen=True)
class VisitRequest:
    visit: SiteVisit
    project: Project
    created: bool


async def approved_project(db: AsyncSession, code: str) -> Project | None:
    rows = (
        await db.execute(
            select(Project).where(Project.code == code, Project.status == RecordStatus.APPROVED)
        )
    ).scalars().all()  # fmt: skip
    return rows[0] if len(rows) == 1 else None


async def request_visit(
    db: AsyncSession,
    config: ClientConfig,
    *,
    lead: Lead,
    customer: Customer,
    project_code: str,
    slot: str,
    visitor_name: str | None,
    channel: Channel,
    now: datetime,
) -> VisitRequest:
    project = await approved_project(db, project_code)
    if project is None:
        raise VisitRequestError("project_unavailable", f"no approved project {project_code!r}")
    existing = await db.scalar(
        select(SiteVisit).where(
            SiteVisit.lead_id == lead.id,
            SiteVisit.project_id == project.id,
            SiteVisit.state.in_(HOLDING_STATES),
        )
    )
    if existing is not None:
        return VisitRequest(existing, project, created=False)
    parsed = parse_slot_id(slot)
    open_ids = {s.id for s in await available_slots(db, config, project_code, now,
                                                    limit=MAX_SLOT_SCAN)}  # fmt: skip
    if parsed is None or slot_id(*parsed) not in open_ids:
        raise VisitRequestError("slot_unavailable", f"slot {slot!r} is not open")
    day, at = parsed
    visit = SiteVisit(
        lead_id=lead.id,
        customer_id=customer.id,
        project_id=project.id,
        visit_date=day,
        slot=at,
        state=VisitState.REQUESTED,
        requested_via=channel,
        visitor_name=visitor_name,
    )
    db.add(visit)
    await db.flush()
    db.add(
        SiteVisitEvent(
            visit_id=visit.id,
            from_state=None,
            to_state=VisitState.REQUESTED,
            actor_type=Actor.SYSTEM.value,
            details={"via": channel.value, "requested_by": "agent"},
        )
    )
    if visitor_name and not customer.name:
        customer.name = visitor_name
    if lead.state != LeadState.VISIT_REQUESTED:
        await transition(
            db, lead, LeadState.VISIT_REQUESTED, actor=Actor.SYSTEM,
            trigger="request_site_visit succeeded", details={"visit_id": str(visit.id)},
        )  # fmt: skip
    await db.flush()
    return VisitRequest(visit, project, created=True)


# ----------------------------------------------------------------------- staff actions

# What staff may do from the dashboard. Only a logged-in staff user ever confirms a visit; a
# reschedule is agreed with the customer by staff, so the new time is confirmed at once.
STAFF_ACTIONS: dict[str, tuple[frozenset[VisitState], VisitState]] = {
    "confirm": (frozenset({VisitState.REQUESTED, VisitState.RESCHEDULED}), VisitState.CONFIRMED),
    "reschedule": (frozenset(HOLDING_STATES), VisitState.CONFIRMED),
    "cancel": (frozenset(HOLDING_STATES), VisitState.CANCELLED),
    "complete": (frozenset({VisitState.CONFIRMED}), VisitState.COMPLETED),
    "no_show": (frozenset({VisitState.CONFIRMED}), VisitState.NO_SHOW),
}


class VisitActionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # not_allowed | slot_unavailable


async def staff_action(
    db: AsyncSession,
    config: ClientConfig,
    visit: SiteVisit,
    action: str,
    *,
    user_id: uuid.UUID,
    now: datetime,
    new_slot: str | None = None,
) -> SiteVisit:
    """Apply a staff action to a visit, record the event and move the lead (ADR 0007).
    Flushes; the caller audits and commits."""
    if action not in STAFF_ACTIONS:
        raise VisitActionError("not_allowed", f"unknown action {action!r}")
    allowed_from, to_state = STAFF_ACTIONS[action]
    if visit.state not in allowed_from:
        raise VisitActionError("not_allowed", f"cannot {action} a visit that is {visit.state}")
    details: dict[str, str] = {"action": action}
    if action == "reschedule":
        project = await db.get(Project, visit.project_id)
        parsed = parse_slot_id(new_slot or "")
        open_ids = {
            s.id for s in await available_slots(db, config, project.code if project else "",
                                                now, limit=MAX_SLOT_SCAN)
        }  # fmt: skip
        if parsed is None or slot_id(*parsed) not in open_ids:
            raise VisitActionError("slot_unavailable", f"slot {new_slot!r} is not open")
        details["from"] = slot_id(visit.visit_date, visit.slot)
        visit.visit_date, visit.slot = parsed
        details["to"] = slot_id(*parsed)
    from_state = visit.state
    visit.state = to_state
    if to_state == VisitState.CONFIRMED:
        visit.confirmed_by, visit.confirmed_at = user_id, now
    db.add(SiteVisitEvent(visit_id=visit.id, from_state=from_state, to_state=to_state,
                          actor_type=Actor.STAFF.value, actor_user_id=user_id,
                          details=details))  # fmt: skip
    lead = await db.get(Lead, visit.lead_id)
    if lead is not None:
        target = {
            VisitState.CONFIRMED: LeadState.VISIT_CONFIRMED,
            VisitState.CANCELLED: LeadState.NURTURING,
        }.get(to_state)
        if target is not None:
            await transition(
                db, lead, target, actor=Actor.STAFF, actor_user_id=user_id,
                trigger=f"visit {action}", details={"visit_id": str(visit.id)},
            )  # fmt: skip
    await db.flush()
    return visit
