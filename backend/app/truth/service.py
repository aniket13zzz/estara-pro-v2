"""Truth Layer: the only source of prices, availability, offers, payment plans, possession dates
and areas for anything customer-facing.

Every answer is either a ``TruthFact`` (approved, valid, non-stale, non-contradicted, with
pre-formatted spoken strings per configured language and its sources) or an ``Unavailable`` with
a reason code: ``missing``, ``stale``, ``unapproved`` or ``contradictory``.

Rows are versioned; a logical key (project code, unit number, ...) may have many rows. For each
key exactly one ``approved`` row may exist. More than one approved row is reported as
``contradictory`` rather than guessed at.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import ClientConfig
from app.core.enums import FactType, RecordStatus
from app.db.models import Amenity, Offer, PaymentPlan, PriceList, Project, Unit, UnitType
from app.truth import formatters as fmt
from app.truth.staleness import Clock, StalenessPolicy, SystemClock


class Reason(StrEnum):
    MISSING = "missing"
    STALE = "stale"
    UNAPPROVED = "unapproved"
    CONTRADICTORY = "contradictory"


@dataclass(frozen=True)
class FactSource:
    table: str
    id: uuid.UUID
    version: int
    batch_id: uuid.UUID | None
    approved_at: datetime


@dataclass(frozen=True)
class TruthFact:
    fact_type: FactType
    subject: dict[str, str]
    value: Any
    display: str
    spoken: dict[str, str]
    source: list[FactSource]
    approved_at: datetime  # oldest approval among the sources
    available: bool = True

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["status"] = "available"
        return out


@dataclass(frozen=True)
class Unavailable:
    fact_type: FactType
    subject: dict[str, str]
    reason: Reason
    detail: str
    available: bool = False

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["status"] = "unavailable"
        return out


TruthResult = TruthFact | Unavailable


@dataclass
class _Pick:
    """Outcome of choosing the current row for one logical key."""

    row: Any | None = None
    reason: Reason | None = None
    detail: str = ""
    sources: list[FactSource] = field(default_factory=list)


class TruthService:
    def __init__(self, db: AsyncSession, config: ClientConfig, clock: Clock | None = None) -> None:
        self.db = db
        self.config = config
        self.clock = clock or SystemClock()
        self.policy = StalenessPolicy(config.truth.stale_after_days, config.company.timezone)
        self.blocked = {FactType(f) for f in config.truth.block_on_stale}
        self.languages = list(config.agent.languages)
        for lang in self.languages:
            fmt.formatter_for(lang)  # raises if a configured language cannot be spoken
        self.now = self.clock.now()

    # ------------------------------------------------------------------------- helpers

    def _spoken(self, build: Any) -> dict[str, str]:
        return {lang: build(fmt.formatter_for(lang)) for lang in self.languages}

    @staticmethod
    def _source(table: str, row: Any) -> FactSource:
        return FactSource(table, row.id, row.version, row.source_batch_id, row.approved_at)

    def _pick(self, table: str, rows: list[Any], fact_type: FactType | None, what: str) -> _Pick:
        """Choose the current row among all versions of one key."""
        approved = [r for r in rows if r.status == RecordStatus.APPROVED]
        if len(approved) > 1:
            return _Pick(
                reason=Reason.CONTRADICTORY, detail=f"{what} has {len(approved)} approved versions"
            )
        if not approved:
            if any(r.status == RecordStatus.DRAFT for r in rows):
                return _Pick(reason=Reason.UNAPPROVED, detail=f"{what} is waiting for approval")
            return _Pick(reason=Reason.MISSING, detail=f"{what} is not in the approved data")
        row = approved[0]
        if self.policy.not_yet_valid(row.valid_from, self.now):
            return _Pick(
                reason=Reason.MISSING,
                detail=f"{what} is not valid until {row.valid_from.isoformat()}",
            )
        if fact_type is not None and fact_type in self.blocked:
            why = self.policy.stale_reason(row.approved_at, row.valid_to, self.now)
            if why:
                return _Pick(reason=Reason.STALE, detail=f"{what} is stale: {why}")
        return _Pick(row=row, sources=[self._source(table, row)])

    def _usable(self, row: Any, fact_type: FactType) -> bool:
        """For aggregates: approved, already valid and (if blocked) not stale."""
        if row.status != RecordStatus.APPROVED or self.policy.not_yet_valid(
            row.valid_from, self.now
        ):
            return False
        return not (
            fact_type in self.blocked
            and self.policy.is_stale(row.approved_at, row.valid_to, self.now)
        )

    def _no_usable(
        self, rows: list[Any], fact_type: FactType, subject: dict[str, str], what: str
    ) -> Unavailable:
        """Why an aggregate found nothing usable: missing, not yet valid, stale or unapproved."""
        approved = [r for r in rows if r.status == RecordStatus.APPROVED]
        if approved:
            future = [r for r in approved if self.policy.not_yet_valid(r.valid_from, self.now)]
            if len(future) == len(approved):
                start = min(r.valid_from for r in future)
                return Unavailable(
                    fact_type,
                    subject,
                    Reason.MISSING,
                    f"{what}: not valid until {start.isoformat()}",
                )
            return Unavailable(
                fact_type, subject, Reason.STALE, f"{what}: every approved row is stale"
            )
        if any(r.status == RecordStatus.DRAFT for r in rows):
            return Unavailable(
                fact_type, subject, Reason.UNAPPROVED, f"{what} is waiting for approval"
            )
        return Unavailable(fact_type, subject, Reason.MISSING, f"{what} is not in the data")

    def _fact(
        self,
        fact_type: FactType,
        subject: dict[str, str],
        value: Any,
        display: str,
        spoken: dict[str, str],
        sources: list[FactSource],
    ) -> TruthFact:
        return TruthFact(
            fact_type=fact_type,
            subject=subject,
            value=value,
            display=display,
            spoken=spoken,
            source=sources,
            approved_at=min(s.approved_at for s in sources),
        )

    # ------------------------------------------------------------------------- loaders

    async def _project_rows(self, code: str) -> list[Project]:
        result = await self.db.execute(select(Project).where(Project.code == code))
        return list(result.scalars())

    async def _project(self, code: str, fact_type: FactType | None) -> _Pick:
        return self._pick("projects", await self._project_rows(code), fact_type, f"Project {code}")

    async def _children(self, model: Any, code: str, *where: Any) -> list[Any]:
        """All versions of child rows whose parent project row has this code."""
        parent = aliased(Project)
        result = await self.db.execute(
            select(model)
            .join(parent, parent.id == model.project_id)
            .where(parent.code == code, *where)
        )
        return list(result.scalars())

    async def _units(self, code: str, unit_number: str | None = None) -> list[Unit]:
        where = [Unit.unit_number == unit_number] if unit_number else []
        return await self._children(Unit, code, *where)

    async def _units_of_type(self, code: str, unit_type_code: str) -> list[Unit]:
        ut = aliased(UnitType)
        parent = aliased(Project)
        result = await self.db.execute(
            select(Unit)
            .join(parent, parent.id == Unit.project_id)
            .join(ut, ut.id == Unit.unit_type_id)
            .where(parent.code == code, ut.code == unit_type_code)
        )
        return list(result.scalars())

    async def _unit_types(self, code: str, unit_type_code: str) -> list[UnitType]:
        return await self._children(UnitType, code, UnitType.code == unit_type_code)

    async def _price_lists(
        self, code: str, *, unit_type_code: str | None = None, unit_number: str | None = None
    ) -> list[PriceList]:
        parent, ut, unit = aliased(Project), aliased(UnitType), aliased(Unit)
        stmt = (
            select(PriceList)
            .join(parent, parent.id == PriceList.project_id)
            .outerjoin(ut, ut.id == PriceList.unit_type_id)
            .outerjoin(unit, unit.id == PriceList.unit_id)
            .where(parent.code == code)
        )
        if unit_number is not None:
            stmt = stmt.where(unit.unit_number == unit_number)
        elif unit_type_code is not None:
            stmt = stmt.where(ut.code == unit_type_code, PriceList.unit_id.is_(None))
        result = await self.db.execute(stmt)
        return list(result.scalars())

    @staticmethod
    def _subject(
        project_code: str, unit_number: str | None, unit_type_code: str | None
    ) -> dict[str, str]:
        subject = {"project_code": project_code}
        if unit_number:
            subject["unit_number"] = unit_number
        if unit_type_code:
            subject["unit_type_code"] = unit_type_code
        return subject

    def _price_fact(
        self, subject: dict[str, str], low: int, high: int, sources: list[FactSource]
    ) -> TruthFact:
        if low == high:
            return self._fact(
                FactType.PRICE,
                subject,
                {"min": low, "max": high},
                fmt.display_inr(low),
                self._spoken(lambda f: f.inr(low)),
                sources,
            )
        return self._fact(
            FactType.PRICE,
            subject,
            {"min": low, "max": high},
            fmt.display_inr_range(low, high),
            self._spoken(lambda f: f.inr_range(low, high)),
            sources,
        )

    # --------------------------------------------------------------------------- price

    async def price(
        self,
        project_code: str,
        *,
        unit_number: str | None = None,
        unit_type_code: str | None = None,
    ) -> TruthResult:
        ft = FactType.PRICE
        subject = self._subject(project_code, unit_number, unit_type_code)
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)

        if unit_number:
            return await self._unit_price(project_code, unit_number, subject)

        if unit_type_code:
            lists = await self._price_lists(project_code, unit_type_code=unit_type_code)
            if lists:
                plist = self._pick("price_lists", lists, ft, f"Price list for {unit_type_code}")
                if plist.row is None:
                    return Unavailable(ft, subject, plist.reason or Reason.MISSING, plist.detail)
                low = plist.row.base_price_inr
                return self._price_fact(subject, low, plist.row.price_max_inr or low, plist.sources)
            units = await self._units_of_type(project_code, unit_type_code)
            return self._aggregate_price(units, [], subject, f"Prices for {unit_type_code}")

        units = await self._units(project_code)
        lists = await self._price_lists(project_code)
        return self._aggregate_price(units, lists, subject, f"Prices for project {project_code}")

    async def _unit_price(
        self, project_code: str, unit_number: str, subject: dict[str, str]
    ) -> TruthResult:
        """Price of one unit. Rules, in order:

        1. The unit must exist with exactly one approved version (else that reason).
        2. A unit-specific price list that is stale, unapproved or contradictory makes the
           price unavailable for that reason (a pending price change must not be ignored).
        3. A price stored on a stale unit row is stale.
        4. If both the unit and its price list give a price and they differ: contradictory.
        5. Otherwise use whichever price exists; with neither, the price is missing.
        """
        ft = FactType.PRICE
        units = await self._units(project_code, unit_number)
        gate = self._pick("units", units, None, f"Unit {unit_number}")
        if gate.row is None:
            return Unavailable(ft, subject, gate.reason or Reason.MISSING, gate.detail)
        unit = gate.row

        lists = await self._price_lists(project_code, unit_number=unit_number)
        plist = self._pick("price_lists", lists, ft, f"Price list for unit {unit_number}")
        if lists and plist.row is None and plist.reason != Reason.MISSING:
            return Unavailable(ft, subject, plist.reason or Reason.MISSING, plist.detail)

        unit_price: int | None = unit.price_inr
        if unit_price is not None and ft in self.blocked:
            why = self.policy.stale_reason(unit.approved_at, unit.valid_to, self.now)
            if why:
                return Unavailable(ft, subject, Reason.STALE, f"Unit {unit_number} is stale: {why}")

        if plist.row is not None:
            low = plist.row.base_price_inr
            high = plist.row.price_max_inr or low
            if unit_price is not None and not (low == unit_price == high):
                return Unavailable(
                    ft,
                    subject,
                    Reason.CONTRADICTORY,
                    f"Unit {unit_number} is priced {unit_price} on the unit but "
                    f"{low}–{high} in its price list",
                )
            sources = plist.sources + (gate.sources if unit_price is not None else [])
            return self._price_fact(subject, low, high, sources)
        if unit_price is None:
            return Unavailable(ft, subject, Reason.MISSING, f"Unit {unit_number} has no price")
        return self._price_fact(subject, unit_price, unit_price, gate.sources)

    def _aggregate_price(
        self, units: list[Unit], lists: list[PriceList], subject: dict[str, str], what: str
    ) -> TruthResult:
        ft = FactType.PRICE
        amounts: list[int] = []
        sources: list[FactSource] = []
        for u in units:
            if u.price_inr is not None and self._usable(u, ft):
                amounts.append(u.price_inr)
                sources.append(self._source("units", u))
        for p in lists:
            if self._usable(p, ft):
                amounts += [p.base_price_inr, p.price_max_inr or p.base_price_inr]
                sources.append(self._source("price_lists", p))
        if not amounts:
            return self._no_usable([*units, *lists], ft, subject, what)
        return self._price_fact(subject, min(amounts), max(amounts), sources)

    # -------------------------------------------------------------------- availability

    async def availability(
        self,
        project_code: str,
        *,
        unit_number: str | None = None,
        unit_type_code: str | None = None,
    ) -> TruthResult:
        ft = FactType.AVAILABILITY
        subject = self._subject(project_code, unit_number, unit_type_code)
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        if unit_number:
            unit = self._pick(
                "units", await self._units(project_code, unit_number), ft, f"Unit {unit_number}"
            )
            if unit.row is None:
                return Unavailable(ft, subject, unit.reason or Reason.MISSING, unit.detail)
            status = unit.row.availability
            words = {
                "en": {
                    "available": "available",
                    "booked": "already booked",
                    "sold": "sold",
                    "blocked": "not available right now",
                },
                "hi": {
                    "available": "उपलब्ध है",
                    "booked": "बुक हो चुका है",
                    "sold": "बिक चुका है",
                    "blocked": "अभी उपलब्ध नहीं है",
                },
            }
            spoken = {lang: words.get(lang, words["en"])[status] for lang in self.languages}
            return self._fact(
                ft, subject, {"status": status}, status.capitalize(), spoken, unit.sources
            )
        units = (
            await self._units_of_type(project_code, unit_type_code)
            if unit_type_code
            else await self._units(project_code)
        )
        usable = [u for u in units if self._usable(u, ft)]
        if not usable:
            return self._no_usable(units, ft, subject, "Unit availability")
        count = sum(1 for u in usable if u.availability == "available")

        def say(f: fmt.SpokenFormatter) -> str:
            if f.code == "hi":
                if count == 0:
                    return "अभी कोई unit उपलब्ध नहीं है"
                verb = "है" if count == 1 else "हैं"
                return f"{f.count(count, 'unit', 'units')} उपलब्ध {verb}"
            if count == 0:
                return "no units available right now"
            return f"{f.count(count, 'unit', 'units')} available"

        return self._fact(
            ft,
            subject,
            {"available": count, "known_units": len(usable)},
            f"{count} available",
            self._spoken(say),
            [self._source("units", u) for u in usable],
        )

    # ------------------------------------------------------------------ offers / plans

    async def offers(self, project_code: str) -> TruthResult:
        ft = FactType.OFFER
        subject = {"project_code": project_code}
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        rows: list[Offer] = await self._children(Offer, project_code)
        current = self._latest_approved_per_key(rows, lambda r: r.title)
        if isinstance(current, Unavailable):
            return Unavailable(ft, subject, current.reason, current.detail)
        usable = [r for r in current if self._usable(r, ft)]
        if not usable:
            return self._no_usable(rows, ft, subject, f"Offers for {project_code}")
        value = [
            {
                "title": o.title,
                "discount_inr": o.discount_inr,
                "discount_percent": str(o.discount_percent) if o.discount_percent else None,
                "valid_to": o.valid_to.isoformat() if o.valid_to else None,
            }
            for o in usable
        ]

        def say(f: fmt.SpokenFormatter) -> str:
            parts = []
            for o in usable:
                text = o.title
                if o.discount_inr:
                    off = f.inr(o.discount_inr)
                    text += f": {off} की छूट" if f.code == "hi" else f": {off} off"
                elif o.discount_percent:
                    off = f.percent(o.discount_percent)
                    text += f": {off} की छूट" if f.code == "hi" else f": {off} off"
                if o.valid_to:
                    until = f.full_date(o.valid_to)
                    text += f", {until} तक" if f.code == "hi" else f", valid till {until}"
                parts.append(text)
            return "; ".join(parts)

        display = "; ".join(o.title for o in usable)
        return self._fact(
            ft,
            subject,
            value,
            display,
            self._spoken(say),
            [self._source("offers", o) for o in usable],
        )

    async def payment_plans(self, project_code: str) -> TruthResult:
        ft = FactType.PAYMENT_PLAN
        subject = {"project_code": project_code}
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        rows: list[PaymentPlan] = await self._children(PaymentPlan, project_code)
        current = self._latest_approved_per_key(rows, lambda r: r.name)
        if isinstance(current, Unavailable):
            return Unavailable(ft, subject, current.reason, current.detail)
        usable = [r for r in current if self._usable(r, ft)]
        if not usable:
            return self._no_usable(rows, ft, subject, f"Payment plans for {project_code}")
        value = [{"name": p.name, "milestones": p.milestones} for p in usable]

        def say(f: fmt.SpokenFormatter) -> str:
            plans = []
            for p in usable:
                steps = []
                for m in p.milestones:
                    pct = f.percent(Decimal(m["percent"]))
                    steps.append(
                        f"{m['stage']} पर {pct}" if f.code == "hi" else f"{pct} at {m['stage']}"
                    )
                plans.append(f"{p.name}: " + ", ".join(steps))
            return "; ".join(plans)

        return self._fact(
            ft,
            subject,
            value,
            "; ".join(p.name for p in usable),
            self._spoken(say),
            [self._source("payment_plans", p) for p in usable],
        )

    @staticmethod
    def _latest_approved_per_key(rows: list[Any], key: Any) -> list[Any] | Unavailable:
        approved: dict[str, list[Any]] = {}
        for r in rows:
            if r.status == RecordStatus.APPROVED:
                approved.setdefault(key(r), []).append(r)
        for k, group in approved.items():
            if len(group) > 1:
                return Unavailable(
                    FactType.OFFER,
                    {},
                    Reason.CONTRADICTORY,
                    f"{k} has {len(group)} approved versions",
                )
        return [g[0] for g in approved.values()]

    async def amenities(self, project_code: str) -> TruthResult:
        """Approved amenities of a project. Names stay as uploaded (usually English)."""
        ft = FactType.AMENITY
        subject = {"project_code": project_code}
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        rows: list[Amenity] = await self._children(Amenity, project_code)
        current = self._latest_approved_per_key(rows, lambda r: r.name)
        if isinstance(current, Unavailable):
            return Unavailable(ft, subject, current.reason, current.detail)
        usable = sorted((r for r in current if self._usable(r, ft)), key=lambda r: r.name)
        if not usable:
            return self._no_usable(rows, ft, subject, f"Amenities for {project_code}")
        names = [a.name for a in usable]
        value = [{"name": a.name, "category": a.category} for a in usable]
        return self._fact(
            ft,
            subject,
            value,
            ", ".join(names),
            self._spoken(lambda f: f.join(names)),
            [self._source("amenities", a) for a in usable],
        )

    # ------------------------------------------------------------------ possession / area

    async def possession(self, project_code: str) -> TruthResult:
        ft = FactType.POSSESSION
        subject = {"project_code": project_code}
        project = await self._project(project_code, ft)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        when: date | None = project.row.possession_date
        if when is None:
            return Unavailable(
                ft, subject, Reason.MISSING, f"Project {project_code} has no possession date"
            )
        return self._fact(
            ft,
            subject,
            when.isoformat(),
            fmt.display_month_year(when),
            self._spoken(lambda f: f.month_year(when)),
            project.sources,
        )

    async def area(
        self,
        project_code: str,
        *,
        unit_number: str | None = None,
        unit_type_code: str | None = None,
    ) -> TruthResult:
        ft = FactType.AREA
        subject = self._subject(project_code, unit_number, unit_type_code)
        project = await self._project(project_code, None)
        if project.row is None:
            return Unavailable(ft, subject, project.reason or Reason.MISSING, project.detail)
        if unit_number:
            pick = self._pick(
                "units", await self._units(project_code, unit_number), ft, f"Unit {unit_number}"
            )
        elif unit_type_code:
            pick = self._pick(
                "unit_types",
                await self._unit_types(project_code, unit_type_code),
                ft,
                f"Unit type {unit_type_code}",
            )
        else:
            return Unavailable(ft, subject, Reason.MISSING, "Area needs a unit or unit type")
        if pick.row is None:
            return Unavailable(ft, subject, pick.reason or Reason.MISSING, pick.detail)
        sqft: Decimal | None = pick.row.carpet_area_sqft
        if sqft is None:
            return Unavailable(ft, subject, Reason.MISSING, "No carpet area recorded")
        value = format(sqft.normalize(), "f")
        return self._fact(
            ft,
            subject,
            {"carpet_area_sqft": value},
            fmt.display_area(sqft),
            self._spoken(lambda f: f.area(sqft)),
            pick.sources,
        )

    # --------------------------------------------------------------------------- entry

    async def get(
        self,
        fact_type: FactType,
        project_code: str,
        *,
        unit_number: str | None = None,
        unit_type_code: str | None = None,
    ) -> TruthResult:
        if fact_type == FactType.PRICE:
            return await self.price(
                project_code, unit_number=unit_number, unit_type_code=unit_type_code
            )
        if fact_type == FactType.AVAILABILITY:
            return await self.availability(
                project_code, unit_number=unit_number, unit_type_code=unit_type_code
            )
        if fact_type == FactType.OFFER:
            return await self.offers(project_code)
        if fact_type == FactType.PAYMENT_PLAN:
            return await self.payment_plans(project_code)
        if fact_type == FactType.POSSESSION:
            return await self.possession(project_code)
        if fact_type == FactType.AMENITY:
            return await self.amenities(project_code)
        return await self.area(project_code, unit_number=unit_number, unit_type_code=unit_type_code)
