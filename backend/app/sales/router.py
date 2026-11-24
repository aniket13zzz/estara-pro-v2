"""Dashboard API for the sales team: leads, calls, site visits, and the browser test call.

Every route needs a logged-in staff user (client_admin or sales_staff). Lists are newest
first and capped. Visit actions are audited.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record as audit
from app.auth.deps import AdminUser, StaffUser
from app.core.config import ClientConfig
from app.core.enums import IdentityType, LeadState, VisitState
from app.core.settings import Settings
from app.db.deps import get_db
from app.db.models import (
    AgentState,
    Call,
    Conversation,
    Customer,
    CustomerIdentity,
    Handoff,
    Lead,
    LeadMatch,
    LeadRequirement,
    Message,
    Objection,
    Project,
    SiteVisit,
)
from app.truth.formatters import display_inr, display_inr_range
from app.truth.staleness import Clock, SystemClock
from app.usage.summary import month_usage
from app.visits.service import VisitActionError, staff_action
from app.visits.slots import available_slots

router = APIRouter(prefix="/api/v1", tags=["sales"])
Db = Annotated[AsyncSession, Depends(get_db)]
LIST_LIMIT = 200


def _config(request: Request) -> ClientConfig:
    cfg: ClientConfig = request.app.state.client_config
    return cfg


def _clock(request: Request) -> Clock:
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return clock


# ------------------------------------------------------------------------------ helpers


async def _phones(db: AsyncSession, customer_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not customer_ids:
        return {}
    rows = await db.execute(
        select(CustomerIdentity.customer_id, CustomerIdentity.value)
        .where(CustomerIdentity.customer_id.in_(customer_ids),
               CustomerIdentity.identity_type.in_([IdentityType.PHONE, IdentityType.WHATSAPP]))
        .order_by(CustomerIdentity.verified.desc(), CustomerIdentity.created_at)
    )  # fmt: skip
    out: dict[uuid.UUID, str] = {}
    for cid, value in rows.tuples():
        out.setdefault(cid, value)
    return out


def _budget(req: LeadRequirement | None) -> str | None:
    if req is None:
        return None
    lo, hi = req.budget_min_inr, req.budget_max_inr
    if lo and hi and lo != hi:
        return display_inr_range(lo, hi)
    if hi or lo:
        return ("up to " if hi and not lo else "") + display_inr(hi or lo or 0)
    return None


def _requirements(req: LeadRequirement | None) -> dict[str, Any]:
    if req is None:
        return {}
    return {
        "budget": _budget(req),
        "locations": req.locations or [],
        "property_type": req.property_type,
        "bhk": req.bhk or [],
        "timeline_months": req.timeline_months,
        "loan_needed": req.loan_needed,
        "purpose": req.purpose,
    }


class LeadRow(BaseModel):
    id: uuid.UUID
    name: str | None
    phone: str | None
    source: str
    state: LeadState
    score: int | None
    band: str | None
    budget: str | None
    bhk: list[int]
    locations: list[str]
    last_contact_at: datetime | None
    next_action: str | None
    created_at: datetime


@router.get("/leads", response_model=list[LeadRow])
async def list_leads(
    _: StaffUser, db: Db, state: Annotated[LeadState | None, Query()] = None
) -> list[LeadRow]:
    query = select(Lead, Customer, LeadRequirement).join(Customer, Customer.id == Lead.customer_id)
    query = query.outerjoin(LeadRequirement, LeadRequirement.lead_id == Lead.id)
    if state is not None:
        query = query.where(Lead.state == state)
    rows = (await db.execute(query.order_by(Lead.updated_at.desc()).limit(LIST_LIMIT))).tuples()
    rows_list = list(rows)
    phones = await _phones(db, [c.id for _, c, _ in rows_list])
    actions: dict[uuid.UUID, str | None] = {}
    lead_ids = [lead.id for lead, _, _ in rows_list]
    if lead_ids:
        for lead_id, context in (await db.execute(
            select(Handoff.lead_id, Handoff.context).where(Handoff.lead_id.in_(lead_ids))
            .order_by(Handoff.created_at.desc())
        )).tuples():  # fmt: skip
            actions.setdefault(lead_id, (context or {}).get("recommended_next_action"))
    return [
        LeadRow(
            id=lead.id,
            name=customer.name,
            phone=phones.get(customer.id),
            source=lead.source_channel.value,
            state=lead.state,
            score=lead.score,
            band=lead.band.value if lead.band else None,
            budget=_budget(req),
            bhk=(req.bhk or []) if req else [],
            locations=(req.locations or []) if req else [],
            last_contact_at=customer.last_contact_at,
            next_action=actions.get(lead.id),
            created_at=lead.created_at,
        )
        for lead, customer, req in rows_list
    ]


def _visit_out(v: SiteVisit, project: Project | None) -> dict[str, Any]:
    return {
        "id": str(v.id), "project": project.name if project else None,
        "project_code": project.code if project else None,
        "date": v.visit_date.isoformat(), "slot": v.slot.strftime("%H:%M"),
        "state": v.state.value, "visitor_name": v.visitor_name, "notes": v.notes,
        "requested_via": v.requested_via.value,
        "confirmed_at": v.confirmed_at.isoformat() if v.confirmed_at else None,
    }  # fmt: skip


def _call_out(c: Call, *, full: bool = False) -> dict[str, Any]:
    out = {
        "id": str(c.id), "vapi_call_id": c.vapi_call_id, "caller_phone": c.caller_phone,
        "customer_id": str(c.customer_id) if c.customer_id else None,
        "language": c.language,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "duration_seconds": c.duration_seconds, "outcome": c.outcome,
        "ended_reason": c.ended_reason, "summary": c.summary_en,
        "recording_url": c.recording_url,
        "cost_usd": float(c.cost_usd) if c.cost_usd is not None else None,
        "unverified_numbers": c.unverified_numbers or [],
    }  # fmt: skip
    if full:
        out["transcript"] = c.transcript
    return out


@router.get("/leads/{lead_id}")
async def lead_detail(lead_id: uuid.UUID, _: StaffUser, db: Db) -> dict[str, Any]:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    customer = await db.get(Customer, lead.customer_id)
    req = await db.scalar(select(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
    matches = (await db.execute(
        select(LeadMatch, Project).join(Project, Project.id == LeadMatch.project_id)
        .where(LeadMatch.lead_id == lead.id).order_by(LeadMatch.created_at.desc(), LeadMatch.rank)
        .limit(9)
    )).tuples()  # fmt: skip
    seen: set[uuid.UUID] = set()
    match_out = []
    for m, p in matches:
        if p.id in seen:
            continue
        seen.add(p.id)
        match_out.append({"rank": m.rank, "project": p.name, "project_code": p.code,
                          "reason": (m.reasons or {}).get("en")})  # fmt: skip
    objections = (await db.execute(
        select(Objection).where(Objection.lead_id == lead.id).order_by(Objection.created_at)
    )).scalars()  # fmt: skip
    visits = (await db.execute(
        select(SiteVisit, Project).outerjoin(Project, Project.id == SiteVisit.project_id)
        .where(SiteVisit.lead_id == lead.id).order_by(SiteVisit.created_at.desc())
    )).tuples()  # fmt: skip
    handoffs = (await db.execute(
        select(Handoff).where(Handoff.lead_id == lead.id).order_by(Handoff.created_at.desc())
    )).scalars().all()  # fmt: skip
    conversations = select(Conversation.id).where(Conversation.customer_id == lead.customer_id)
    messages = (await db.execute(
        select(Message).where(Message.conversation_id.in_(conversations))
        .order_by(Message.created_at).limit(500)
    )).scalars()  # fmt: skip
    calls = (await db.execute(
        select(Call).where(Call.customer_id == lead.customer_id).order_by(Call.created_at.desc())
    )).scalars()  # fmt: skip
    phones = await _phones(db, [lead.customer_id])
    return {
        "id": str(lead.id),
        "customer": {"name": customer.name if customer else None,
                     "phone": phones.get(lead.customer_id),
                     "preferred_language": customer.preferred_language if customer else None},
        "state": lead.state.value, "score": lead.score,
        "band": lead.band.value if lead.band else None, "score_reason": lead.score_reason,
        "source": lead.source_channel.value,
        "requirements": _requirements(req),
        "matches": match_out,
        "objections": [{"type": o.objection_type, "text": o.text, "language": o.language,
                        "at": o.created_at.isoformat()} for o in objections],
        "visits": [_visit_out(v, p) for v, p in visits],
        "handoffs": [{"id": str(h.id), "reason": h.reason, "status": h.status.value,
                      "assigned_email": h.assigned_email, "created_at": h.created_at.isoformat(),
                      "next_action": (h.context or {}).get("recommended_next_action")}
                     for h in handoffs],
        "timeline": [{"at": m.created_at.isoformat(), "channel": m.channel.value,
                      "from": m.sender.value, "language": m.language, "text": m.content}
                     for m in messages],
        "calls": [_call_out(c) for c in calls],
    }  # fmt: skip


@router.get("/calls")
async def list_calls(_: StaffUser, db: Db) -> list[dict[str, Any]]:
    calls = (await db.execute(
        select(Call).order_by(Call.created_at.desc()).limit(LIST_LIMIT)
    )).scalars()  # fmt: skip
    return [_call_out(c) for c in calls]


@router.get("/calls/{call_id}")
async def call_detail(call_id: uuid.UUID, _: StaffUser, db: Db) -> dict[str, Any]:
    call = await db.get(Call, call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    out = _call_out(call, full=True)
    lead_id = None
    if call.customer_id:
        lead_id = await db.scalar(select(Lead.id).where(Lead.customer_id == call.customer_id)
                                  .order_by(Lead.created_at.desc()).limit(1))  # fmt: skip
    out["lead_id"] = str(lead_id) if lead_id else None
    return out


@router.get("/visits")
async def list_visits(
    _: StaffUser, db: Db, state: Annotated[VisitState | None, Query()] = None
) -> list[dict[str, Any]]:
    query = (select(SiteVisit, Project, Customer)
             .outerjoin(Project, Project.id == SiteVisit.project_id)
             .join(Customer, Customer.id == SiteVisit.customer_id))  # fmt: skip
    if state is not None:
        query = query.where(SiteVisit.state == state)
    rows = list((await db.execute(
        query.order_by(SiteVisit.visit_date, SiteVisit.slot).limit(LIST_LIMIT)
    )).tuples())  # fmt: skip
    phones = await _phones(db, [c.id for _, _, c in rows])
    return [{**_visit_out(v, p), "lead_id": str(v.lead_id), "customer_name": c.name,
             "phone": phones.get(c.id)} for v, p, c in rows]  # fmt: skip


@router.get("/visits/{visit_id}/slots")
async def reschedule_slots(visit_id: uuid.UUID, request: Request, _: StaffUser,
                           db: Db) -> list[dict[str, str]]:  # fmt: skip
    visit = await db.get(SiteVisit, visit_id)
    project = await db.get(Project, visit.project_id) if visit else None
    if visit is None or project is None:
        raise HTTPException(404, "Visit not found")
    slots = await available_slots(db, _config(request), project.code, _clock(request).now(),
                                  limit=12)  # fmt: skip
    return [{"slot_id": s.id, "date": s.day.isoformat(), "time": s.at.strftime("%H:%M")}
            for s in slots]  # fmt: skip


class VisitAction(BaseModel):
    action: Literal["confirm", "reschedule", "cancel", "complete", "no_show"]
    slot_id: str | None = None


@router.post("/visits/{visit_id}/action")
async def visit_action(visit_id: uuid.UUID, body: VisitAction, request: Request,
                       auth: StaffUser, db: Db) -> dict[str, Any]:  # fmt: skip
    visit = await db.get(SiteVisit, visit_id)
    if visit is None:
        raise HTTPException(404, "Visit not found")
    before = (visit.state.value, visit.visit_date.isoformat(), visit.slot.strftime("%H:%M"))
    try:
        await staff_action(db, _config(request), visit, body.action, user_id=auth.user.id,
                           now=_clock(request).now(), new_slot=body.slot_id)  # fmt: skip
    except VisitActionError as exc:
        raise HTTPException(409, str(exc)) from None
    await audit(db, f"visit.{body.action}", actor_user_id=auth.user.id, entity_type="site_visit",
                entity_id=visit.id, details={"before": before, "after": visit.state.value},
                ip_address=request.client.host if request.client else None)  # fmt: skip
    await db.commit()
    project = await db.get(Project, visit.project_id)
    return _visit_out(visit, project)


# ---------------------------------------------------------------------------- test call


@router.get("/voice/test-call")
async def test_call_settings(request: Request, _: StaffUser) -> dict[str, Any]:
    """What the dashboard needs to start a browser test call: Vapi's *public* key (made for
    browsers) and the assistant id. Never the private key or the shared secret."""
    settings: Settings = request.app.state.settings
    config = _config(request)
    missing = []
    if config.voice.mode == "custom_llm" and not config.voice.custom_ready:
        missing.append("voice settings in client.config.yaml (voice.tts and voice.stt)")
    if not settings.vapi_public_key:
        missing.append("VAPI_PUBLIC_KEY in .env")
    if not settings.vapi_assistant_id:
        missing.append("VAPI_ASSISTANT_ID in .env (run the sync command)")
    return {
        "ready": not missing, "missing": missing,
        "public_key": settings.vapi_public_key if not missing else None,
        "assistant_id": settings.vapi_assistant_id if not missing else None,
    }  # fmt: skip


# ------------------------------------------------------------------------ home, controls

QUALIFIED_OR_LATER = (LeadState.QUALIFIED, LeadState.VISIT_REQUESTED, LeadState.VISIT_CONFIRMED,
                      LeadState.HANDED_OFF, LeadState.WON)  # fmt: skip


@router.get("/summary")
async def summary(request: Request, _: StaffUser, db: Db) -> dict[str, Any]:
    """Home page: today's numbers, the visit-booking rate, usage against limits, pause state."""
    config, now = _config(request), _clock(request).now()
    tz = ZoneInfo(config.company.timezone)
    today = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    since_30 = now - timedelta(days=30)

    async def count(query: Any) -> int:
        return int(await db.scalar(query) or 0)

    leads_30 = select(Lead.id).where(Lead.created_at >= since_30)
    with_visit = select(SiteVisit.lead_id).where(SiteVisit.lead_id.in_(leads_30))
    qualified = await count(select(func.count(Lead.id)).where(
        Lead.created_at >= since_30,
        or_(Lead.state.in_(QUALIFIED_OR_LATER), Lead.id.in_(with_visit)),
    ))  # fmt: skip
    booked = await count(select(func.count(func.distinct(SiteVisit.lead_id)))
                         .where(SiteVisit.lead_id.in_(leads_30)))  # fmt: skip
    state = await db.get(AgentState, 1)
    usage = await month_usage(db, config, now)
    return {
        "today": {
            "new_leads": await count(select(func.count(Lead.id)).where(Lead.created_at >= today)),
            "calls": await count(select(func.count(Call.id)).where(Call.created_at >= today)),
            "visits_requested": await count(select(func.count(SiteVisit.id))
                                            .where(SiteVisit.created_at >= today)),
            "visits_confirmed": await count(select(func.count(SiteVisit.id))
                                            .where(SiteVisit.confirmed_at >= today)),
        },
        "waiting_confirmation": await count(select(func.count(SiteVisit.id))
                                            .where(SiteVisit.state == VisitState.REQUESTED)),
        "last_30_days": {"qualified_leads": qualified, "visits_booked": booked,
                         "visit_booking_rate": round(booked / qualified, 3) if qualified else None},
        "usage": usage.as_dict(),
        "paused": bool(state and state.paused),
        "paused_reason": state.paused_reason if state and state.paused else None,
    }  # fmt: skip


class PauseRequest(BaseModel):
    paused: bool
    reason: str | None = None


@router.post("/controls/pause")
async def set_paused(body: PauseRequest, request: Request, auth: AdminUser,
                     db: Db) -> dict[str, Any]:  # fmt: skip
    """The pause button (client admin only, audited). While paused, every call goes straight to
    the human number and chat channels get the paused message."""
    state = await db.get(AgentState, 1)
    if state is None:
        state = AgentState(id=1, paused=False)
        db.add(state)
    state.paused = body.paused
    state.paused_reason = (body.reason or "").strip()[:500] or None if body.paused else None
    state.updated_by = auth.user.id
    await audit(db, "agent.pause" if body.paused else "agent.resume",
                actor_user_id=auth.user.id, entity_type="agent_state", entity_id="1",
                details={"reason": state.paused_reason},
                ip_address=request.client.host if request.client else None)  # fmt: skip
    await db.commit()
    return {"paused": state.paused, "paused_reason": state.paused_reason}
