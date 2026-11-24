"""Lead lifecycle: the transition table from ADR 0007, as data.

Only listed transitions are allowed, and only for the listed actors. ``system`` means backend
rules acting on tool results and events; the LLM never names a target state. ``staff`` means an
authenticated client_admin or sales_staff user. Every transition, and every rejected attempt,
is written to ``lead_events``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Channel, LeadState
from app.db.models import Customer, Lead, LeadEvent


class Actor(StrEnum):
    SYSTEM = "system"
    STAFF = "staff"


S = LeadState
SYSTEM = frozenset({Actor.SYSTEM})
STAFF = frozenset({Actor.STAFF})
BOTH = frozenset({Actor.SYSTEM, Actor.STAFF})

TRANSITIONS: dict[tuple[LeadState, LeadState], frozenset[Actor]] = {
    (S.NEW_ENQUIRY, S.ENGAGED): SYSTEM,
    (S.NEW_ENQUIRY, S.QUALIFIED): SYSTEM,
    (S.NEW_ENQUIRY, S.HANDED_OFF): BOTH,
    (S.NEW_ENQUIRY, S.LOST): STAFF,
    (S.ENGAGED, S.QUALIFIED): SYSTEM,
    (S.ENGAGED, S.NURTURING): BOTH,
    (S.ENGAGED, S.HANDED_OFF): BOTH,
    (S.ENGAGED, S.LOST): STAFF,
    (S.QUALIFIED, S.VISIT_REQUESTED): BOTH,
    (S.QUALIFIED, S.NURTURING): BOTH,
    (S.QUALIFIED, S.HANDED_OFF): BOTH,
    (S.QUALIFIED, S.WON): STAFF,
    (S.QUALIFIED, S.LOST): STAFF,
    (S.VISIT_REQUESTED, S.VISIT_CONFIRMED): STAFF,
    (S.VISIT_REQUESTED, S.NURTURING): BOTH,
    (S.VISIT_REQUESTED, S.HANDED_OFF): BOTH,
    (S.VISIT_REQUESTED, S.WON): STAFF,
    (S.VISIT_REQUESTED, S.LOST): STAFF,
    (S.VISIT_CONFIRMED, S.VISIT_REQUESTED): BOTH,
    (S.VISIT_CONFIRMED, S.NURTURING): BOTH,
    (S.VISIT_CONFIRMED, S.HANDED_OFF): BOTH,
    (S.VISIT_CONFIRMED, S.WON): STAFF,
    (S.VISIT_CONFIRMED, S.LOST): STAFF,
    (S.HANDED_OFF, S.VISIT_REQUESTED): BOTH,
    (S.HANDED_OFF, S.VISIT_CONFIRMED): STAFF,
    (S.HANDED_OFF, S.NURTURING): STAFF,
    (S.HANDED_OFF, S.WON): STAFF,
    (S.HANDED_OFF, S.LOST): STAFF,
    (S.NURTURING, S.ENGAGED): SYSTEM,
    (S.NURTURING, S.QUALIFIED): SYSTEM,
    (S.NURTURING, S.VISIT_REQUESTED): BOTH,
    (S.NURTURING, S.HANDED_OFF): BOTH,
    (S.NURTURING, S.WON): STAFF,
    (S.NURTURING, S.LOST): STAFF,
}
TERMINAL: frozenset[LeadState] = frozenset({S.WON, S.LOST})


class TransitionRejected(Exception):
    def __init__(self, from_state: LeadState, to_state: LeadState, actor: Actor) -> None:
        super().__init__(f"{actor} cannot move a lead from {from_state} to {to_state}")
        self.from_state = from_state
        self.to_state = to_state
        self.actor = actor


def allowed(from_state: LeadState, to_state: LeadState, actor: Actor) -> bool:
    return actor in TRANSITIONS.get((from_state, to_state), frozenset())


@dataclass(frozen=True)
class TransitionResult:
    ok: bool
    from_state: LeadState
    to_state: LeadState
    changed: bool


async def transition(
    db: AsyncSession,
    lead: Lead,
    to_state: LeadState,
    *,
    actor: Actor,
    trigger: str,
    actor_user_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    strict: bool = False,
) -> TransitionResult:
    """Move ``lead`` to ``to_state`` if ADR 0007 allows it. Flushes; the caller commits.

    Rejected attempts are logged as ``transition_rejected`` and the lead keeps its state. With
    ``strict=True`` (staff actions) a rejection raises ``TransitionRejected`` after logging.
    """
    from_state = lead.state
    if from_state == to_state:
        return TransitionResult(True, from_state, to_state, changed=False)
    if actor == Actor.STAFF and actor_user_id is None:
        raise ValueError("staff transitions need the acting user")
    ok = allowed(from_state, to_state, actor)
    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type="transition" if ok else "transition_rejected",
            from_state=from_state,
            to_state=to_state,
            actor_type=actor.value,
            actor_user_id=actor_user_id,
            details={"trigger": trigger, **(details or {})},
        )
    )
    if ok:
        lead.state = to_state
    await db.flush()
    if not ok and strict:
        raise TransitionRejected(from_state, to_state, actor)
    return TransitionResult(ok, from_state, to_state, changed=ok)


async def open_lead(db: AsyncSession, customer: Customer) -> Lead | None:
    result = await db.execute(
        select(Lead)
        .where(Lead.customer_id == customer.id, Lead.state.not_in(list(TERMINAL)))
        .order_by(Lead.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_open_lead(db: AsyncSession, customer: Customer, channel: Channel) -> Lead:
    """The customer's open lead, or a new ``new_enquiry`` lead (ADR 0007 rules 5 and 6)."""
    lead = await open_lead(db, customer)
    if lead is not None:
        return lead
    lead = Lead(customer_id=customer.id, state=S.NEW_ENQUIRY, source_channel=channel)
    db.add(lead)
    await db.flush()
    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type="created",
            to_state=S.NEW_ENQUIRY,
            actor_type=Actor.SYSTEM.value,
            details={"channel": channel.value},
        )
    )
    await db.flush()
    return lead
