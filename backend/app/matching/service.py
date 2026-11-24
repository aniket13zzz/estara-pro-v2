"""Property matching: structured search over approved inventory, then explainable ranking.

Search (SQL): approved, available units with approved, non-stale availability, grouped into
options of one project and one unit type. Filters: requested BHK and property type. Prices,
possession and areas come from the Truth Layer, so an option whose price is stale, unapproved
or contradictory is never presented as fitting a budget.

Ranking (100 points, deterministic):

| Component | Points | Rule |
| --- | --- | --- |
| Budget fit | 40 | price overlaps the budget; 20 if at most 10% above; unknown budget 20 |
| Location | 30 | locality matches a requested location; 15 city only; unknown 15 |
| BHK | 20 | requested BHK; unknown 10 |
| Possession vs timeline | 10 | ready to move or within the timeline; unknown timeline 5 |

Options more than 10% over budget, or far below a stated minimum, are left out. Each option
carries a reason in every configured language.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import FactType, RecordStatus
from app.db.models import Lead, LeadMatch, Project, Unit, UnitType
from app.leads.requirements import Requirements
from app.matching.phrases import PHRASES, check_languages
from app.truth import formatters as fmt
from app.truth.service import TruthFact, TruthService
from app.truth.staleness import Clock

TOP_N = 3
OVER_BUDGET_STRETCH = 1.10
UNDER_MIN_FLOOR = 0.80


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _edits(a: str, b: str) -> int:
    """Levenshtein distance (small strings only)."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _sounds_like(wanted: str, place: str) -> bool:
    """Speech-to-text misspellings of a place: "Newton" for "New Town". Compared with runs of
    whole words of the real name, allowing one wrong letter (two for long names), and only for
    names of five letters or more, so short names never match by accident."""
    if len(wanted) < 5:
        return False
    words = [norm(x) for x in place.split() if norm(x)]
    allowed = 2 if len(wanted) >= 9 else 1
    for start in range(len(words)):
        for end in range(start + 1, len(words) + 1):
            run = "".join(words[start:end])
            if abs(len(run) - len(wanted)) <= allowed and _edits(wanted, run) <= allowed:
                return True
    return False


def location_matches(wanted: str, locality: str | None, city: str) -> str | None:
    """ "locality", "city" or None."""
    w = norm(wanted)
    if not w:
        return None
    if locality:
        loc = norm(locality)
        if w in loc or (len(loc) >= 4 and loc in w) or _sounds_like(w, locality):
            return "locality"
    if w == norm(city) or _sounds_like(w, city):
        return "city"
    return None


@dataclass
class Option:
    project_code: str
    project_name: str
    locality: str | None
    city: str
    unit_type_code: str | None
    unit_type_name: str
    bhk: int
    property_type: str
    available_units: int
    representative_unit_id: uuid.UUID
    price: TruthFact | None
    possession: TruthFact | None
    area: TruthFact | None
    score: int = 0
    components: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)

    def as_tool_result(self, language: str) -> dict[str, Any]:
        return {
            "project_code": self.project_code,
            "project": self.project_name,
            "unit_type_code": self.unit_type_code,
            "bhk": self.bhk,
            "price": self.price.spoken[language] if self.price else None,
            "area": self.area.spoken[language] if self.area else None,
            "possession": self.possession.spoken[language] if self.possession else None,
            "reason": self.reasons[language],
            "match_score": self.score,
        }


def _months_until(today: date, when: date) -> int:
    return (when.year - today.year) * 12 + (when.month - today.month)


class Matcher:
    def __init__(self, db: AsyncSession, config: ClientConfig, clock: Clock) -> None:
        check_languages(list(config.agent.languages))
        self.db = db
        self.config = config
        self.truth = TruthService(db, config, clock)
        self.now: datetime = self.truth.now
        self.today = self.now.astimezone(ZoneInfo(config.company.timezone)).date()

    async def _candidates(self, req: Requirements) -> list[Option]:
        projects = (
            await self.db.execute(select(Project).where(Project.status == RecordStatus.APPROVED))
        ).scalars().all()  # fmt: skip
        current = {p.code: p for p in projects}
        code_by_project_id = {
            pid: code for pid, code in (await self.db.execute(select(Project.id, Project.code)))
        }
        unit_types = (await self.db.execute(select(UnitType))).scalars().all()
        ut_code = {u.id: u.code for u in unit_types}
        ut_name = {
            (code_by_project_id.get(u.project_id), u.code): u.name
            for u in unit_types
            if u.status == RecordStatus.APPROVED
        }
        stmt = select(Unit).where(
            Unit.status == RecordStatus.APPROVED, Unit.availability == "available"
        )
        if req.bhk:
            stmt = stmt.where(Unit.bhk.in_(req.bhk))
        if req.property_type:
            stmt = stmt.where(Unit.property_type == req.property_type)
        units = (await self.db.execute(stmt)).scalars().all()

        groups: dict[tuple[str, str], list[Unit]] = defaultdict(list)
        for unit in units:
            code = code_by_project_id.get(unit.project_id)
            if code not in current or not self.truth._usable(unit, FactType.AVAILABILITY):
                continue
            type_code = (ut_code.get(unit.unit_type_id) if unit.unit_type_id else None) or (
                f"{unit.bhk}BHK-{unit.property_type}"
            )
            groups[(code, type_code)].append(unit)

        options = []
        for (code, type_code), members in groups.items():
            project = current[code]
            has_type = any(u.unit_type_id for u in members)
            cheapest = min(members, key=lambda u: u.price_inr or 0)
            price = (
                await self.truth.price(code, unit_type_code=type_code)
                if has_type
                else self.truth._aggregate_price(members, [], {"project_code": code}, "Prices")
            )
            area = await self.truth.area(code, unit_type_code=type_code) if has_type else None
            possession = await self.truth.possession(code)
            options.append(
                Option(
                    project_code=code,
                    project_name=project.name,
                    locality=project.locality,
                    city=project.city,
                    unit_type_code=type_code if has_type else None,
                    unit_type_name=ut_name.get((code, type_code), ""),
                    bhk=cheapest.bhk,
                    property_type=cheapest.property_type,
                    available_units=len(members),
                    representative_unit_id=cheapest.id,
                    price=price if isinstance(price, TruthFact) else None,
                    possession=possession if isinstance(possession, TruthFact) else None,
                    area=area if isinstance(area, TruthFact) else None,
                )
            )
        return options

    def _score(self, option: Option, req: Requirements) -> bool:
        """Fill score, components and reasons. False if the option should be left out."""
        parts: dict[str, list[str]] = {lang: [] for lang in self.config.agent.languages}

        def add(key: str, **kw: str) -> None:
            for lang in parts:
                parts[lang].append(PHRASES[lang][key].format(**kw))

        # Budget
        lo_b, hi_b = req.budget_min_inr, req.budget_max_inr
        if lo_b is None and hi_b is None:
            budget = 20
        elif option.price is None:
            return False  # cannot verify the fit with an unavailable price
        else:
            lo, hi = option.price.value["min"], option.price.value["max"]
            if hi_b is not None and lo > hi_b * OVER_BUDGET_STRETCH:
                return False
            if lo_b is not None and hi < lo_b * UNDER_MIN_FLOOR:
                return False
            within = (hi_b is None or lo <= hi_b) and (lo_b is None or hi >= lo_b * UNDER_MIN_FLOOR)
            budget = 40 if within else 20
        # Location
        if req.locations:
            hits = {location_matches(w, option.locality, option.city) for w in req.locations}
            location = 30 if "locality" in hits else 15 if "city" in hits else 0
        else:
            location = 15
        # BHK
        bhk = 20 if req.bhk and option.bhk in req.bhk else 10
        # Possession vs timeline
        when = date.fromisoformat(option.possession.value) if option.possession else None
        if when is None or req.timeline_months is None:
            timing = 5
        elif when <= self.today or _months_until(self.today, when) <= req.timeline_months:
            timing = 10
        else:
            timing = 0

        option.components = {"budget": budget, "location": location, "bhk": bhk, "timing": timing}
        option.score = budget + location + bhk + timing

        add("option", name=option.unit_type_name or f"{option.bhk} BHK",
            project=option.project_name, locality=option.locality or option.city)  # fmt: skip
        for lang in parts:
            if option.price:
                parts[lang].append(option.price.spoken[lang])
            else:
                parts[lang].append(PHRASES[lang]["price_tbc"])
        if budget == 40 and (lo_b or hi_b):
            add("within_budget")
        elif budget == 20 and (lo_b or hi_b):
            add("above_budget")
        if req.locations:
            if location == 30:
                add("preferred_area")
            elif location < 15:
                add("other_area", locality=option.locality or option.city,
                    wanted=", ".join(req.locations))  # fmt: skip
        if option.possession and when is not None:
            for lang in parts:
                spoken = option.possession.spoken[lang]
                if when <= self.today:
                    key = "ready"
                elif req.timeline_months is None:
                    key = "possession"
                else:
                    key = "possession_ok" if timing == 10 else "possession_late"
                parts[lang].append(PHRASES[lang][key].format(when=spoken))
        for lang in parts:
            count = fmt.formatter_for(lang).count(option.available_units, "unit", "units")
            parts[lang].append(PHRASES[lang]["available"].format(count=count))
        option.reasons = {lang: ", ".join(p for p in ps if p) for lang, ps in parts.items()}
        return True

    async def rank(self, req: Requirements) -> list[Option]:
        options = [o for o in await self._candidates(req) if self._score(o, req)]
        options.sort(
            key=lambda o: (-o.score, o.price.value["min"] if o.price else 0, o.project_code)
        )
        return options

    async def search(self, req: Requirements, *, top: int = TOP_N) -> list[Option]:
        return (await self.rank(req))[:top]


async def save_matches(db: AsyncSession, lead: Lead, options: list[Option]) -> None:
    """Keep each search's results on the lead (history is kept; the newest set is current)."""
    projects = {
        code: pid
        for pid, code in (
            await db.execute(
                select(Project.id, Project.code).where(Project.status == RecordStatus.APPROVED)
            )
        ).tuples()
    }
    for rank, option in enumerate(options, start=1):
        db.add(
            LeadMatch(
                lead_id=lead.id,
                project_id=projects[option.project_code],
                unit_id=option.representative_unit_id,
                rank=rank,
                match_score=option.score,
                reasons=option.reasons,
            )
        )
    await db.flush()
