"""The Sales Brain's tools: the phase plan's tool contract, shared by every channel.

Each tool is a JSON-Schema spec for the LLM plus a backend handler. Handlers do the work with
deterministic backend code (Truth Layer, matching, scoring, visits, handoffs) and return short
results in the conversation language. Facts arrive as pre-formatted spoken strings under
``say``; anything the Truth Layer cannot serve comes back as ``status: unavailable`` with an
instruction to confirm with the sales team. The model never receives a raw number it is
expected to phrase itself, and the LLM never names a lead state: state changes happen inside
the handlers by ADR 0007 rules.

Every call runs in a savepoint, so a failing tool rolls back only its own writes and returns a
structured error, never a stack trace.

On calls these tools run inside the Sales Brain too: Vapi sends each caller turn to the
custom-LLM endpoint (app/channels/vapi), so the tools are never exposed to Vapi directly.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import zip_longest
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.extraction import parse_budget_text
from app.brain.scripts import Scripts
from app.core.config import ClientConfig
from app.core.enums import FactType, IdentityType, LeadState, RecordStatus
from app.customers import memory
from app.datacenter.knowledge import search_knowledge
from app.db.models import (
    Conversation,
    Customer,
    CustomerIdentity,
    Handoff,
    Lead,
    Project,
    SiteVisit,
    UnitType,
)
from app.handoff.follow_ups import schedule_follow_up
from app.handoff.service import create_handoff, in_working_hours
from app.leads import requirements as reqs
from app.leads.requirements import Requirements
from app.leads.scoring import advance, evaluate
from app.leads.state import Actor, transition
from app.matching.service import Matcher, Option, location_matches, save_matches
from app.providers.embeddings import Embedder, EmbeddingsNotConfigured
from app.providers.llm import ToolSpec
from app.truth.service import TruthService, Unavailable
from app.truth.staleness import Clock
from app.visits.service import VisitRequestError, approved_project, request_visit
from app.visits.slots import HOLDING_STATES, available_slots

log = logging.getLogger(__name__)

CONFIRM_WITH_TEAM = (
    "Tell the customer you will confirm this with the sales team and get back to them. "
    "Do not guess or give any number."
)
MAX_SLOTS_OFFERED = 4
HANDOFF_STATES = (
    LeadState.NEW_ENQUIRY, LeadState.ENGAGED, LeadState.QUALIFIED, LeadState.VISIT_REQUESTED,
    LeadState.VISIT_CONFIRMED, LeadState.NURTURING,
)  # fmt: skip


@dataclass
class ToolContext:
    db: AsyncSession
    config: ClientConfig
    clock: Clock
    now: datetime
    customer: Customer
    conversation: Conversation
    lead: Lead
    language: str
    scripts: Scripts
    embedder: Callable[[], Embedder] | None = None
    handoffs: list[Handoff] = field(default_factory=list)
    visit_requested: bool = False
    unavailable: int = 0


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


class _Args(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ------------------------------------------------------------------------------ arguments


class NoArgs(_Args):
    pass


PROPERTY_TYPE_WORDS = {
    "apartment": "apartment", "apartments": "apartment", "flat": "apartment", "flats": "apartment",
    "villa": "villa", "house": "villa", "independent house": "villa", "bungalow": "villa",
    "plot": "plot", "land": "plot", "row house": "row_house", "row_house": "row_house",
    "rowhouse": "row_house", "penthouse": "penthouse", "studio": "studio", "1rk": "studio",
}  # fmt: skip
PURPOSE_WORDS = {"self_use": "self_use", "self use": "self_use", "own use": "self_use",
                 "living": "self_use", "family": "self_use", "end use": "self_use",
                 "investment": "investment", "invest": "investment",
                 "rental": "investment", "rental income": "investment"}  # fmt: skip
LOAN_WORDS = {"needed": "needed", "yes": "needed", "need loan": "needed", "loan": "needed",
              "not_needed": "not_needed", "no": "not_needed", "no loan": "not_needed",
              "pre_approved": "pre_approved", "pre-approved": "pre_approved",
              "preapproved": "pre_approved"}  # fmt: skip


# "Any area is fine": search everywhere instead of treating the words as a place name.
ANY_LOCATION = re.compile(
    r"^(any|anywhere|any ?(where|place|location|area|available location)s?|all( areas| locations)?"
    r"|whatever|no preference|doesn'?t matter|does not matter|kahi(n)? bhi|kahin bhi|koi bhi"
    r"|कहीं भी|कोई भी)\b",
    re.IGNORECASE,
)
TIMELINE_WORDS = {"immediately": 0, "ready": 0, "ready to move": 0, "now": 0, "asap": 0,
                  "next month": 1, "this month": 0, "a month": 1, "one month": 1,
                  "two months": 2, "three months": 3, "six months": 6, "next year": 12,
                  "a year": 12, "one year": 12}  # fmt: skip


def _any_location(value: str) -> bool:
    words = " ".join(str(value).lower().replace("-", " ").split())
    return bool(ANY_LOCATION.match(words))


ARG_ALIASES = (("location", "locations"), ("area", "locations"), ("budget", "budget_text"),
               ("timeline", "timeline_months"))  # fmt: skip


def _as_list(value: Any) -> Any:
    if value is None or isinstance(value, list):
        return value
    if isinstance(value, str) and "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return [value]


def _word(value: Any, words: dict[str, str]) -> str | None:
    """A known word, or None (an unknown value is dropped, never an error for the caller)."""
    if not isinstance(value, str):
        return None
    key = " ".join(value.lower().replace("-", " ").replace("_", " ").split())
    return words.get(key) or words.get(key.replace(" ", "_"))


class RequirementArgs(_Args):
    """Lenient on purpose: voice models send "2" for [2], "flat" for "apartment", a string for a
    list. Such shapes are accepted; a value that cannot be understood is dropped, not refused."""

    @model_validator(mode="before")
    @classmethod
    def _tolerate_shapes(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for alias, name in ARG_ALIASES:
            if alias in data and name not in data:
                data[name] = data.pop(alias)
        if data.get("budget_text") is not None and not isinstance(data["budget_text"], str):
            data["budget_text"] = str(data["budget_text"])
        locations = _as_list(data.get("locations")) or []
        if any(isinstance(x, str) and _any_location(x) for x in locations):
            data["any_location"] = True
        data["locations"] = [x for x in locations
                             if not (isinstance(x, str) and _any_location(x))] or None  # fmt: skip
        bhk = []
        for item in _as_list(data.get("bhk")) or []:
            found = re.search(r"\d+", str(item))
            if found:
                bhk.append(int(found.group()))
        data["bhk"] = bhk or None
        if "property_type" in data:
            data["property_type"] = _word(data["property_type"], PROPERTY_TYPE_WORDS)
        timeline = data.get("timeline_months")
        if timeline is not None:
            digits = re.search(r"\d+", str(timeline))
            spoken = " ".join(str(timeline).lower().split())
            months = int(digits.group()) if digits else TIMELINE_WORDS.get(spoken)
            data["timeline_months"] = months if months is not None and months <= 120 else None
        for key, words in (("purpose", PURPOSE_WORDS), ("loan", LOAN_WORDS)):
            if key in data:
                data[key] = _word(data[key], words)
        return data

    budget_text: str | None = Field(
        default=None,
        description="The customer's own words about budget, e.g. '80 lakh', 'ek se dedh crore'. "
        "Never convert it to a number yourself.",
    )
    locations: list[str] | None = Field(
        default=None,
        description="Area or city names in English letters, e.g. 'Rajarhat' for 'राजारहाट'.",
    )
    any_location: bool = Field(
        default=False,
        description="true when the caller says any area is fine: search all our locations.",
    )
    property_type: Literal["apartment", "villa", "plot", "row_house", "penthouse",
                           "studio"] | None = None  # fmt: skip
    bhk: list[int] | None = None
    timeline_months: int | None = Field(default=None, ge=0, le=120,
                                        description="0 means ready to move")  # fmt: skip

    def requirements(self) -> Requirements:
        lo, hi = parse_budget_text(self.budget_text) if self.budget_text else (None, None)
        return Requirements(
            budget_min_inr=lo,
            budget_max_inr=hi,
            locations=[x.strip() for x in self.locations or [] if x.strip()],
            property_type=self.property_type,
            bhk=[b for b in self.bhk or [] if 0 < b <= 6],
            timeline_months=self.timeline_months,
        )


class SaveRequirementsArgs(RequirementArgs):
    loan: Literal["needed", "not_needed", "pre_approved"] | None = None
    purpose: Literal["self_use", "investment"] | None = None
    customer_name: str | None = Field(default=None, max_length=200)

    def requirements(self) -> Requirements:
        req = super().requirements()
        req.loan = self.loan
        req.purpose = self.purpose
        return req


class ProjectArgs(_Args):
    project_code: str = Field(min_length=1, max_length=50)


class DetailsArgs(ProjectArgs):
    unit_type_code: str | None = None


class CommercialArgs(ProjectArgs):
    fact_type: FactType
    unit_type_code: str | None = None
    unit_number: str | None = None


class KnowledgeArgs(_Args):
    question: str = Field(min_length=2, max_length=500)


class VisitArgs(ProjectArgs):
    slot_id: str = Field(description="A slot_id exactly as returned by get_visit_slots")
    visitor_name: str | None = Field(default=None, max_length=200)
    whatsapp_ok: bool | None = Field(
        default=None,
        description="On a call: true only if the customer said yes to receiving the visit "
        "details on WhatsApp at this number; false if they said no; omit if not asked.",
    )
    visitors: int | None = Field(
        default=None, ge=1, le=20, description="How many people will come, if they said."
    )


class TransferArgs(_Args):
    reason: Literal["human_requested", "angry", "legal", "out_of_scope", "cannot_help",
                    "other"] = "human_requested"  # fmt: skip
    note: str | None = Field(default=None, max_length=300)


class CallbackArgs(_Args):
    phone: str | None = Field(
        default=None,
        max_length=40,
        description="The caller's own mobile number as they confirmed it, digits only. "
        "Omit on a phone call if they want a call back on the number they are calling from.",
    )
    preferred_time: str | None = Field(
        default=None, max_length=100, description="When they prefer the call, in their words."
    )


class FollowUpArgs(_Args):
    days_from_now: int | None = Field(default=None, ge=1, le=60)
    channel: str | None = None


# -------------------------------------------------------------------------------- helpers


def unavailable(fact_type: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "fact_type": fact_type, "reason": reason,
            "instruction": CONFIRM_WITH_TEAM, **extra}  # fmt: skip


def _fact(ctx: ToolContext, result: Any) -> dict[str, Any]:
    if isinstance(result, Unavailable):
        ctx.unavailable += 1
        return unavailable(result.fact_type.value, result.reason.value)
    return {"status": "available", "fact_type": result.fact_type.value,
            "say": result.spoken[ctx.language]}  # fmt: skip


async def _saved(ctx: ToolContext) -> Requirements:
    return await reqs.load(ctx.db, ctx.lead)


MONEY_IN_TEXT = re.compile(
    r"(₹|\brs\.?|\binr\b)\s*\d[\d,]*(\.\d+)?(\s*(lakhs?|lacs?|crores?|cr|l|k)\b)?"
    r"|\d[\d,]*(\.\d+)?\s*(lakhs?|lacs?|crores?|cr|लाख|करोड़)"
    r"|\d+(\.\d+)?\s*(%|percent)"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(19|20)\d\d",
    re.IGNORECASE,
)


def mask_commercial(text: str) -> str:
    """Documents never supply prices, percentages or dates (CLAUDE.md): mask them."""
    return MONEY_IN_TEXT.sub("[ask get_commercial_info]", text)


# ---------------------------------------------------------------------------------- tools


async def get_customer_context(ctx: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
    req = await _saved(ctx)
    visit = await ctx.db.scalar(
        select(SiteVisit).where(SiteVisit.lead_id == ctx.lead.id,
                                SiteVisit.state.in_(HOLDING_STATES))
    )  # fmt: skip
    summary = memory.summary(ctx.customer)
    return {
        "returning_customer": summary["last_contact_at"] is not None,
        "name": ctx.customer.name,
        "preferred_language": ctx.customer.preferred_language,
        "lead_state": ctx.lead.state.value,
        "requirements_known": [f for f in reqs.FIELD_NAMES if req.known(f)],
        "missing_for_qualification": req.missing(list(ctx.config.lead_scoring.qualified_requires)),
        "objections_raised": summary["objections"],
        "properties_discussed": summary["properties_discussed"],
        "pending_visit": visit is not None,
    }


async def search_properties(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    parsed = RequirementArgs.model_validate(args)
    wanted = parsed.requirements()
    if parsed.any_location:
        await reqs.clear_locations(ctx.db, ctx.lead)
    if wanted.is_empty():
        req = await _saved(ctx)
    else:
        # What the caller asked for is their requirement: save it (no separate save call, so a
        # voice turn needs one tool round trip, not two). Newer values win.
        req, _ = await reqs.save(ctx.db, ctx.lead, wanted, source="llm")
        memory.remember_requirements(ctx.customer, req.model_dump())
        await advance(ctx.db, ctx.lead, req, ctx.config)
    options = await Matcher(ctx.db, ctx.config, ctx.clock).search(req)
    area_note = await _unknown_areas(ctx, req.locations)
    if options:
        await save_matches(ctx.db, ctx.lead, options)
        memory.remember_properties(
            ctx.customer,
            [{"project": o.project_code, "unit_type": o.unit_type_code or ""} for o in options],
        )
        matches = []
        for option in options:
            item = await _with_availability(ctx, option)
            differs = _differs(option, req)
            if differs:
                item["differs"] = differs
            matches.append(item)
        exact = not matches[0].get("differs")
        return {
            "matches": matches,
            "exact_match": exact,
            "visit_slots": await _first_slots(ctx, options[0].project_code),
            "instruction": MATCHES_INSTRUCTION if exact else NEAR_MATCHES_INSTRUCTION,
            **area_note,
        }
    alternatives = await _closest_alternatives(ctx, req)
    if not alternatives:
        return {
            "matches": [],
            "instruction": "No approved option fits. Ask whether budget, "
            "location or BHK is flexible. Do not suggest projects that were not returned.",
            **area_note,
        }
    return {
        "matches": [],
        "alternatives": alternatives,
        "visit_slots": await _first_slots(ctx, alternatives[0]["project_code"]),
        "instruction": ALTERNATIVES_INSTRUCTION,
        **area_note,
    }


MATCHES_INSTRUCTION = (
    "Present the best one or two like a confident salesperson: name the first, say why it suits "
    "them using its reason, price and availability exactly as given, and recommend seeing it. "
    "Then invite a visit with the two visit_slots. Do not read out a list or repeat their "
    "requirements back to them."
)
NEAR_MATCHES_INSTRUCTION = (
    "Nothing fits every requirement exactly. Say so in one short sentence (for example, no 3 BHK "
    "in their area right now), then present the best one or two of these as real alternatives: "
    "say plainly what is different (its 'differs': another area or above budget) and why it "
    "could still suit them, using its reason and price exactly as given. Ask if they would like "
    "to see it, offering the two visit_slots. Never call an alternative a match."
)


def _differs(option: Option, req: reqs.Requirements) -> list[str]:
    """How a search result differs from what was asked: 'location' (another area) or
    'budget' (above it), so the agent presents it honestly as an alternative."""
    out = []
    if req.locations and option.components.get("location", 0) == 0:
        out.append("location")
    if (req.budget_max_inr or req.budget_min_inr) and option.components.get("budget") == 20:
        out.append("budget")
    return out


async def _with_availability(ctx: ToolContext, option: Option) -> dict[str, Any]:
    """The option plus how many homes of that type are available, from the Truth Layer (so the
    agent can mention real availability, never invented scarcity)."""
    out = option.as_tool_result(ctx.language)
    if option.unit_type_code:
        fact = await TruthService(ctx.db, ctx.config, ctx.clock).availability(
            option.project_code, unit_type_code=option.unit_type_code
        )
        if not isinstance(fact, Unavailable):
            out["availability"] = fact.spoken[ctx.language]
    return out


async def _unknown_areas(ctx: ToolContext, wanted: list[str]) -> dict[str, Any]:
    """Area names that match no approved project (for example written in Devanagari, or
    misheard): name the areas we do have, so the agent can search again with the right one."""
    rows = (await ctx.db.execute(
        select(Project.locality, Project.city).where(Project.status == RecordStatus.APPROVED)
    )).tuples().all()  # fmt: skip
    unknown = [w for w in wanted
               if not any(location_matches(w, loc, city) for loc, city in rows)]  # fmt: skip
    if not unknown:
        return {}
    known = sorted({name for pair in rows for name in pair if name})
    return {
        "unknown_areas": unknown,
        "our_areas": known,
        "area_instruction": "These area names match none of our projects. If the caller meant "
        "one of our_areas (for example 'न्यू टाउन' or 'Newton' is New Town), call "
        "search_properties again with that English name. Otherwise say we have no project there "
        "and offer the options returned.",
    }


SLOTS_WITH_SEARCH = 2


async def _first_slots(ctx: ToolContext, project_code: str) -> dict[str, Any]:
    """The next open visit slots for the first option, so the agent can invite a visit without
    another lookup (each lookup is a pause on a phone call)."""
    from app.truth.formatters import formatter_for

    f = formatter_for(ctx.language)
    slots = await available_slots(ctx.db, ctx.config, project_code, ctx.now,
                                  limit=SLOTS_WITH_SEARCH)  # fmt: skip
    return {"project_code": project_code,
            "slots": [{"slot_id": s.id, "slot": f.slot(s.day, s.at)} for s in slots]}  # fmt: skip


ALTERNATIVES_INSTRUCTION = (
    "Nothing fits every requirement exactly. Say so honestly in one short sentence, then offer "
    "the one or two closest alternatives below as real options. For each, say plainly what is "
    "different (its 'differs' field: a higher budget or a smaller size) and why it could still "
    "suit them, using its reason, price and area exactly as given. Then invite them to see it on "
    "a site visit. Never say an alternative matches what they asked, and never offer a discount "
    "or deal that a tool did not give."
)


async def _closest_alternatives(ctx: ToolContext, req: reqs.Requirements) -> list[dict[str, Any]]:
    """Nothing fits: the nearest real options a salesperson would offer, each labelled with what
    differs. Same size at the next budget step, and a smaller size within the budget."""
    matcher = Matcher(ctx.db, ctx.config, ctx.clock)
    above: list[Option] = []
    if req.budget_max_inr:
        stretch = req.model_copy(update={"budget_min_inr": None, "budget_max_inr": None})
        above = [o for o in await matcher.rank(stretch)
                 if o.price and o.price.value["min"] > req.budget_max_inr]  # fmt: skip
        # The next budget step first; among equal steps, the better fit (area, timing).
        above.sort(key=lambda o: (o.price.value["min"] if o.price else 0, -o.score))
    sizes: list[Option] = []
    if req.bhk:
        wanted = req.bhk
        others = [o for o in await matcher.rank(req.model_copy(update={"bhk": []}))
                  if o.bhk not in wanted]  # fmt: skip
        # The nearest size first (a 2 BHK before a 1 BHK for someone who asked for 3).
        others.sort(key=lambda o: (min(abs(o.bhk - w) for w in wanted), -o.score))
        sizes = others
    # Alternate the two kinds: best higher-budget option, best smaller one, then the next.
    found: list[tuple[str, Option]] = []
    for budget_option, size_option in zip_longest(above[:2], sizes[:2]):
        if budget_option is not None:
            found.append(("budget", budget_option))
        if size_option is not None:
            found.append(("size", size_option))
    out: list[dict[str, Any]] = []
    kept: list[Option] = []
    seen: set[tuple[str, str | None]] = set()
    for differs, option in found:
        key = (option.project_code, option.unit_type_code)
        if key in seen or len(kept) == 3:
            continue
        seen.add(key)
        kept.append(option)
        out.append({**option.as_tool_result(ctx.language), "differs": differs})
    if kept:
        await save_matches(ctx.db, ctx.lead, kept)
    return out


async def get_property_details(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = DetailsArgs.model_validate(args)
    project = await approved_project(ctx.db, a.project_code)
    if project is None:
        ctx.unavailable += 1
        return unavailable("project", "missing", project_code=a.project_code)
    truth = TruthService(ctx.db, ctx.config, ctx.clock)
    types = (
        await ctx.db.execute(
            select(UnitType.code, UnitType.name, UnitType.bhk)
            .where(UnitType.project_id == project.id, UnitType.status == RecordStatus.APPROVED)
            .order_by(UnitType.bhk, UnitType.code)
        )
    ).tuples().all()  # fmt: skip
    out: dict[str, Any] = {
        "project_code": project.code,
        "project": project.name,
        "locality": project.locality,
        "city": project.city,
        "description": project.description,
        "unit_types": [{"unit_type_code": c, "name": n, "bhk": b} for c, n, b in types],
        "possession": _fact(ctx, await truth.possession(project.code)),
        "amenities": _fact(ctx, await truth.amenities(project.code)),
    }
    if a.unit_type_code:
        code = a.unit_type_code
        out["unit_type_code"] = code
        out["price"] = _fact(ctx, await truth.price(project.code, unit_type_code=code))
        out["area"] = _fact(ctx, await truth.area(project.code, unit_type_code=code))
        out["availability"] = _fact(
            ctx, await truth.availability(project.code, unit_type_code=code)
        )
    return out


async def get_commercial_info(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = CommercialArgs.model_validate(args)
    truth = TruthService(ctx.db, ctx.config, ctx.clock)
    result = await truth.get(a.fact_type, a.project_code, unit_number=a.unit_number,
                             unit_type_code=a.unit_type_code)  # fmt: skip
    return {"project_code": a.project_code, **_fact(ctx, result)}


async def search_knowledge_tool(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = KnowledgeArgs.model_validate(args)
    if ctx.embedder is None:
        return {"status": "unavailable", "reason": "knowledge_search_not_configured",
                "instruction": CONFIRM_WITH_TEAM}  # fmt: skip
    try:
        embedder = ctx.embedder()
    except EmbeddingsNotConfigured:
        return {"status": "unavailable", "reason": "knowledge_search_not_configured",
                "instruction": CONFIRM_WITH_TEAM}  # fmt: skip
    hits = await search_knowledge(ctx.db, embedder, a.question)
    if not hits:
        return {"status": "unavailable", "reason": "no_matching_document",
                "instruction": CONFIRM_WITH_TEAM}  # fmt: skip
    return {
        "snippets": [{"title": h.title, "text": mask_commercial(h.content[:600])} for h in hits],
        "instruction": "Answer only from these snippets. Prices, offers, dates and availability "
        "come only from get_commercial_info, never from documents.",
    }


async def save_requirements(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = SaveRequirementsArgs.model_validate(args)
    if a.customer_name and a.customer_name.strip():
        ctx.customer.name = a.customer_name.strip()
    merged, changed = await reqs.save(ctx.db, ctx.lead, a.requirements(), source="llm")
    memory.remember_requirements(ctx.customer, merged.model_dump())
    await advance(ctx.db, ctx.lead, merged, ctx.config)
    missing = merged.missing(list(ctx.config.lead_scoring.qualified_requires))
    return {
        "saved": changed,
        "qualified": not missing,
        "missing": missing,
        "ask_next": missing[0] if missing else None,
        "lead_state": ctx.lead.state.value,
    }


async def evaluate_lead(ctx: ToolContext, _: dict[str, Any]) -> dict[str, Any]:
    req = await _saved(ctx)
    ev = await evaluate(ctx.db, ctx.lead, req, ctx.config, ctx.clock, now=ctx.now)
    return {"score": ev.score, "band": ev.band.value, "qualified": ev.qualified,
            "missing": ev.missing, "lead_state": ctx.lead.state.value}  # fmt: skip


async def get_visit_slots(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = ProjectArgs.model_validate(args)
    project = await approved_project(ctx.db, a.project_code)
    if project is None:
        ctx.unavailable += 1
        return unavailable("project", "missing", project_code=a.project_code)
    from app.truth.formatters import formatter_for

    f = formatter_for(ctx.language)
    slots = await available_slots(ctx.db, ctx.config, project.code, ctx.now,
                                  limit=MAX_SLOTS_OFFERED)  # fmt: skip
    if not slots:
        return {
            "project_code": project.code,
            "slots": [],
            "instruction": "No open slots. Offer to have the sales team call to arrange one.",
        }
    return {
        "project_code": project.code,
        "project": project.name,
        "slots": [{"slot_id": s.id, "slot": f.slot(s.day, s.at)} for s in slots],
        "instruction": "Offer two or three of these exact slots. Visits are confirmed by the "
        "sales team, so say it will be requested, not booked.",
    }


async def request_site_visit(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = VisitArgs.model_validate(args)
    req = await _saved(ctx)
    missing = req.missing(list(ctx.config.lead_scoring.qualified_requires))
    if missing:
        return {"status": "needs_requirements", "missing": missing,
                "instruction": "Ask for the missing requirements first."}  # fmt: skip
    name = (a.visitor_name or ctx.customer.name or "").strip() or None
    if name is None:
        return {"status": "needs_name", "instruction": "Ask for the visitor's name first."}
    try:
        result = await request_visit(
            ctx.db, ctx.config, lead=ctx.lead, customer=ctx.customer,
            project_code=a.project_code, slot=a.slot_id, visitor_name=name,
            channel=ctx.conversation.channel, now=ctx.now,
        )  # fmt: skip
    except VisitRequestError as exc:
        if exc.code == "slot_unavailable":
            fresh = await get_visit_slots(ctx, {"project_code": a.project_code})
            return {"status": "slot_unavailable", "slots": fresh.get("slots", []),
                    "instruction": "That slot is not open. Offer these instead."}  # fmt: skip
        ctx.unavailable += 1
        return unavailable("project", "missing", project_code=a.project_code)
    ctx.visit_requested = True
    if a.visitors and result.created:
        result.visit.notes = f"Visitors: {a.visitors}"
    if a.whatsapp_ok and not ctx.customer.whatsapp_opted_out:
        ctx.customer.whatsapp_opt_in_at = ctx.customer.whatsapp_opt_in_at or ctx.now
    handoff = await create_handoff(
        ctx.db, ctx.config, lead=ctx.lead, customer=ctx.customer,
        conversation=ctx.conversation, reason="visit_requested", now=ctx.now,
        details={"visit_id": str(result.visit.id),
                 **({"visitors": a.visitors} if a.visitors else {})},
    )  # fmt: skip
    ctx.handoffs.append(handoff)
    from app.truth.formatters import formatter_for

    f = formatter_for(ctx.language)
    return {
        "status": "requested" if result.created else "already_requested",
        "project": result.project.name,
        "slot": f.slot(result.visit.visit_date, result.visit.slot),
        "visitor_name": result.visit.visitor_name,
        "instruction": "Tell the customer the visit is requested and the sales team will "
        "confirm it. Do not say it is confirmed.",
    }


async def transfer_to_human(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = TransferArgs.model_validate(args)
    handoff = await create_handoff(
        ctx.db, ctx.config, lead=ctx.lead, customer=ctx.customer, conversation=ctx.conversation,
        reason=a.reason, now=ctx.now, details={"note": a.note} if a.note else None,
    )  # fmt: skip
    ctx.handoffs.append(handoff)
    if ctx.lead.state in HANDOFF_STATES:
        await transition(ctx.db, ctx.lead, LeadState.HANDED_OFF, actor=Actor.SYSTEM,
                         trigger=f"transfer_to_human: {a.reason}")  # fmt: skip
    open_now = in_working_hours(ctx.config, ctx.now)
    out: dict[str, Any] = {"status": "handed_off", "within_working_hours": open_now}
    if open_now:
        out["instruction"] = (
            "Tell the customer a member of the sales team will contact them "
            "shortly. Do not promise a time."
        )
    else:
        out["instruction"] = "Our team is away. Use this script, in the customer's language."
        out["script"] = ctx.scripts.after_hours(ctx.language)
    return out


async def request_callback(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """A salesperson calls the customer back. Saved as a handoff with the number, so it shows on
    the dashboard; the answer says "callback_saved" only once it is stored (the request's
    database transaction commits before the reply is sent)."""
    from app.customers.identity import country_code_of, normalise_phone

    a = CallbackArgs.model_validate(args)
    known = await ctx.db.scalar(
        select(CustomerIdentity.value).where(
            CustomerIdentity.customer_id == ctx.customer.id,
            CustomerIdentity.identity_type == IdentityType.PHONE,
            CustomerIdentity.verified.is_(True),
        )
    )
    if a.phone and a.phone.strip():
        cc = country_code_of(ctx.config.escalation.human_transfer_number)
        number = normalise_phone(a.phone, cc)
        digits = re.sub(r"\D", "", a.phone)
        if number is None or (cc == "91" and not re.fullmatch(r"(91|0)?[6-9]\d{9}", digits)):
            return {"status": "invalid_number",
                    "instruction": "That is not a valid mobile number. Ask for the 10-digit "
                    "mobile number again, read it back in two groups, and confirm it."}  # fmt: skip
        if number != known:
            exists = await ctx.db.scalar(
                select(CustomerIdentity.id).where(
                    CustomerIdentity.customer_id == ctx.customer.id,
                    CustomerIdentity.identity_type == IdentityType.PHONE,
                    CustomerIdentity.value == number,
                )
            )
            if exists is None:
                # Said on the call, not proved by the network: stored unverified (ADR 0010).
                ctx.db.add(CustomerIdentity(customer_id=ctx.customer.id,
                                            identity_type=IdentityType.PHONE, value=number,
                                            verified=False))  # fmt: skip
                await ctx.db.flush()
    elif known:
        number = known
    else:
        return {"status": "needs_number",
                "instruction": "Ask for their 10-digit mobile number, read it back in two groups "
                "(for example 98300, 12345), and call request_callback again once they "
                "confirm it."}  # fmt: skip
    details = {"phone": number, "preferred_time": (a.preferred_time or "").strip() or None}
    handoff = await create_handoff(
        ctx.db, ctx.config, lead=ctx.lead, customer=ctx.customer, conversation=ctx.conversation,
        reason="callback_requested", now=ctx.now, details=details,
    )  # fmt: skip
    ctx.handoffs.append(handoff)
    if ctx.lead.state in HANDOFF_STATES:
        await transition(ctx.db, ctx.lead, LeadState.HANDED_OFF, actor=Actor.SYSTEM,
                         trigger="request_callback")  # fmt: skip
    return {
        "status": "callback_saved",
        "number_ends_with": number[-4:],
        "within_working_hours": in_working_hours(ctx.config, ctx.now),
        "instruction": "Tell them it is booked: our sales team will call them on the number "
        "ending with these four digits. Do not promise an exact time.",
    }


async def schedule_follow_up_tool(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    a = FollowUpArgs.model_validate(args)
    result = await schedule_follow_up(
        ctx.db, ctx.config, lead=ctx.lead, customer=ctx.customer, language=ctx.language,
        now=ctx.now, days=a.days_from_now, channel=a.channel,
    )  # fmt: skip
    if result.follow_up is None:
        return {"status": result.status,
                "instruction": "Do not promise a follow-up message."}  # fmt: skip
    return {
        "status": "scheduled",
        "channel": result.follow_up.channel.value,
        "instruction": "Tell the customer the team will follow up. Do not name a date.",
    }


# ------------------------------------------------------------------------------- registry


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    props = {k: _plain(v) for k, v in schema.get("properties", {}).items()}
    out: dict[str, Any] = {"type": "object", "properties": props}
    if schema.get("required"):
        out["required"] = schema["required"]
    return out


def _plain(prop: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits ``anyOf [X, null]`` for optional fields; providers want a plain type."""
    prop = {k: v for k, v in prop.items() if k not in {"title", "default"}}
    if "anyOf" in prop:
        options = [o for o in prop.pop("anyOf") if o.get("type") != "null"]
        if len(options) == 1:
            prop.update(options[0])
    if "$ref" in prop:
        prop.pop("$ref")
        prop["type"] = "string"
    if prop.get("type") == "array" and "items" in prop:
        prop["items"] = _plain(prop["items"])
    return prop


def _commercial_schema() -> dict[str, Any]:
    schema = _schema(CommercialArgs)
    schema["properties"]["fact_type"] = {"type": "string", "enum": [f.value for f in FactType]}
    return schema


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Handler
    model: type[BaseModel]


def _tool(name: str, description: str, model: type[BaseModel], handler: Handler,
          schema: dict[str, Any] | None = None) -> Tool:  # fmt: skip
    return Tool(ToolSpec(name, description, schema or _schema(model)), handler, model)


TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in (
        _tool("get_customer_context", "What we already know about this customer: lead state, "
              "known and missing requirements, objections, properties discussed, pending visit.",
              NoArgs, get_customer_context),
        _tool("search_properties", "Top 3 approved options for the requirements, with a reason "
              "each. Pass what the caller asked for; it is saved, so no save_requirements call "
              "is needed for it.",
              RequirementArgs, search_properties),
        _tool("get_property_details", "Approved details of one project (and optionally one "
              "unit type): unit types, possession, amenities, price, area, availability.",
              DetailsArgs, get_property_details),
        _tool("get_commercial_info", "One approved fact: price, availability, offer, "
              "payment_plan, possession, area or amenity, for a project, unit type or unit.",
              CommercialArgs, get_commercial_info, _commercial_schema()),
        _tool("search_knowledge", "Up to three snippets from approved documents (brochures, "
              "FAQs) for questions that are not prices, offers, dates or availability.",
              KnowledgeArgs, search_knowledge_tool),
        _tool("save_requirements", "Save what the customer said they need. Pass budget as the "
              "customer's own words in budget_text.", SaveRequirementsArgs, save_requirements),
        _tool("evaluate_lead", "Deterministic lead score, band and qualification.",
              NoArgs, evaluate_lead),
        _tool("get_visit_slots", "Open site-visit slots for a project.", ProjectArgs,
              get_visit_slots),
        _tool("request_site_visit", "Request a site visit in a slot from get_visit_slots. The "
              "sales team confirms it.", VisitArgs, request_site_visit),
        _tool("transfer_to_human", "Hand the conversation to the sales team: the customer asks "
              "for a person, is upset, asks a legal question, or you cannot help.",
              TransferArgs, transfer_to_human),
        _tool("request_callback", "Ask a salesperson to phone the customer back. Use when they "
              "want a call back or a person on a website call. Confirm the number with them "
              "first.", CallbackArgs, request_callback),
        _tool("schedule_follow_up", "Schedule a WhatsApp follow-up message (not a phone call) "
              "when the customer asks to be messaged later.", FollowUpArgs,
              schedule_follow_up_tool),
    )
}  # fmt: skip


def tool_specs() -> list[ToolSpec]:
    return [t.spec for t in TOOLS.values()]


async def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Run one tool in a savepoint. Unknown tools, bad arguments and failures come back as
    structured results the model can act on."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"status": "error", "error": f"unknown tool {name!r}"}
    try:
        tool.model.model_validate(args)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return {"status": "error", "error": f"invalid arguments: {problems}"[:400]}
    try:
        async with ctx.db.begin_nested():
            return await tool.handler(ctx, args)
    except Exception:
        log.exception("tool %s failed", name)
        return {"status": "error", "error": "lookup failed", "instruction": CONFIRM_WITH_TEAM}


def result_size(result: dict[str, Any]) -> int:
    return len(json.dumps(result, ensure_ascii=False))
