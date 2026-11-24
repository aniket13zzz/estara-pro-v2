"""Deterministic lead scoring and qualification from ``lead_scoring`` config (ADR 0005, 0007).

Each weight key is a yes/no rule checked by the backend:

| Rule | Met when |
| --- | --- |
| ``budget_match`` | at least one approved, available option fits the budget (Truth Layer prices) |
| ``location_match`` | at least one approved project is in a requested location |
| ``timeline_under_6m`` | the customer wants to move within 6 months (ready to move counts) |
| ``loan_ready`` | the customer pays without a loan, or the loan is already approved |
| ``visit_requested`` | the lead has a site visit that is not cancelled |

Score = 100 × (sum of weights met) / (sum of all weights), rounded. Band: hot at or above
``thresholds.hot``, warm at or above ``thresholds.warm``, else cold. Qualified = every field in
``qualified_requires`` is known. The reason lists every rule with met / not met, so staff can
see exactly why. Scoring never changes the lead state (ADR 0007 rule 7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import LeadBand, LeadState, RecordStatus, VisitState
from app.db.models import Lead, LeadEvent, Project, SiteVisit
from app.leads.requirements import Requirements
from app.leads.state import Actor, transition
from app.matching.service import Matcher, location_matches
from app.truth.staleness import Clock


@dataclass(frozen=True)
class Evaluation:
    score: int
    band: LeadBand
    qualified: bool
    missing: list[str]
    rules: dict[str, bool]
    reason: str


def band_for(score: int, config: ClientConfig) -> LeadBand:
    t = config.lead_scoring.thresholds
    if score >= t.hot:
        return LeadBand.HOT
    if score >= t.warm:
        return LeadBand.WARM
    return LeadBand.COLD


async def _location_match(db: AsyncSession, req: Requirements) -> bool:
    if not req.locations:
        return False
    rows = await db.execute(
        select(Project.locality, Project.city).where(Project.status == RecordStatus.APPROVED)
    )
    return any(location_matches(w, loc, city) for loc, city in rows.tuples() for w in req.locations)


async def _budget_match(db: AsyncSession, config: ClientConfig, req: Requirements,
                        clock: Clock) -> bool:  # fmt: skip
    if not req.known("budget"):
        return False
    budget_only = Requirements(budget_min_inr=req.budget_min_inr, budget_max_inr=req.budget_max_inr)
    options = await Matcher(db, config, clock).rank(budget_only)
    return any(o.components.get("budget") == 40 for o in options)


async def _visit_requested(db: AsyncSession, lead: Lead) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(SiteVisit)
        .where(SiteVisit.lead_id == lead.id, SiteVisit.state != VisitState.CANCELLED)
    )
    return bool(count)


async def evaluate(
    db: AsyncSession, lead: Lead, req: Requirements, config: ClientConfig, clock: Clock,
    *, now: datetime, log_unchanged: bool = True,
) -> Evaluation:  # fmt: skip
    """Score the lead, store score, band and reason, and log a ``scored`` event (with
    ``log_unchanged=False``, only when the score or band changed)."""
    before = (lead.score, lead.band)
    weights = config.lead_scoring.weights
    rules = {
        "budget_match": await _budget_match(db, config, req, clock),
        "location_match": await _location_match(db, req),
        "timeline_under_6m": req.timeline_months is not None and req.timeline_months <= 6,
        "loan_ready": req.loan in ("not_needed", "pre_approved"),
        "visit_requested": await _visit_requested(db, lead),
    }
    total = sum(weights.values())
    earned = sum(w for key, w in weights.items() if rules.get(key))
    score = round(100 * earned / total)
    band = band_for(score, config)
    missing = req.missing(list(config.lead_scoring.qualified_requires))
    reason = (
        "; ".join(
            f"{key} {'met' if rules.get(key) else 'not met'} ({weights[key]})" for key in weights
        )
        + f" = {score}/100, {band.value}"
    )
    lead.score, lead.band, lead.score_reason, lead.scored_at = score, band, reason, now
    if log_unchanged or before != (score, band):
        db.add(
            LeadEvent(
                lead_id=lead.id,
                event_type="scored",
                actor_type=Actor.SYSTEM.value,
                details={"score": score, "band": band.value, "rules": rules, "missing": missing},
            )
        )
    await db.flush()
    return Evaluation(score, band, not missing, missing, rules, reason)


async def advance(db: AsyncSession, lead: Lead, req: Requirements, config: ClientConfig) -> None:
    """System transitions driven by known requirements (ADR 0007):
    new_enquiry → engaged on the first requirement; → qualified when every required field is
    known (from new_enquiry, engaged or nurturing)."""
    required = list(config.lead_scoring.qualified_requires)
    if req.is_empty():
        return
    if not req.missing(required) and lead.state in (
        LeadState.NEW_ENQUIRY, LeadState.ENGAGED, LeadState.NURTURING,
    ):  # fmt: skip
        await transition(db, lead, LeadState.QUALIFIED, actor=Actor.SYSTEM,
                         trigger="all required requirements known")  # fmt: skip
    elif lead.state == LeadState.NEW_ENQUIRY:
        await transition(db, lead, LeadState.ENGAGED, actor=Actor.SYSTEM,
                         trigger="first requirement saved")  # fmt: skip
