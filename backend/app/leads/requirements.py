"""Customer requirements: one Pydantic model, stored on ``lead_requirements``.

Amounts are whole rupees. ``timeline_months`` 0 means ready to move. ``loan`` distinguishes a
customer who needs a loan from one who already has it approved (that is what makes a buyer
"loan ready" for scoring).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadEvent, LeadRequirement

Loan = Literal["needed", "not_needed", "pre_approved"]
Purpose = Literal["self_use", "investment"]

# Field names that `lead_scoring.qualified_requires` may refer to (config validates these).
FIELD_NAMES: tuple[str, ...] = (
    "budget", "location", "property_type", "bhk", "timeline", "loan", "purpose",
)  # fmt: skip


class Requirements(BaseModel):
    budget_min_inr: int | None = Field(default=None, gt=0)
    budget_max_inr: int | None = Field(default=None, gt=0)
    locations: list[str] = Field(default_factory=list)
    property_type: str | None = None
    bhk: list[int] = Field(default_factory=list)
    timeline_months: int | None = Field(default=None, ge=0)
    loan: Loan | None = None
    purpose: Purpose | None = None

    def known(self, field: str) -> bool:
        if field == "budget":
            return self.budget_min_inr is not None or self.budget_max_inr is not None
        if field == "location":
            return bool(self.locations)
        if field == "bhk":
            return bool(self.bhk)
        if field == "timeline":
            return self.timeline_months is not None
        return getattr(self, field) is not None

    def missing(self, fields: list[str]) -> list[str]:
        return [f for f in fields if not self.known(f)]

    def merge(self, newer: Requirements) -> tuple[Requirements, list[str]]:
        """Newer values win; returns the merged requirements and the fields that changed."""
        data = self.model_dump()
        changed: list[str] = []
        for key, value in newer.model_dump().items():
            if value in (None, []):
                continue
            if key == "locations":
                value = list(dict.fromkeys([*value]))  # newest statement replaces the list
            if data.get(key) != value:
                data[key] = value
                changed.append(key)
        merged = Requirements.model_validate(data)
        lo, hi = merged.budget_min_inr, merged.budget_max_inr
        if lo is not None and hi is not None and lo > hi:
            # A new upper bound below an old lower bound: keep only the newest statement.
            if "budget_max_inr" in changed and "budget_min_inr" not in changed:
                merged.budget_min_inr = None
            else:
                merged.budget_max_inr = None
        return merged, changed

    def is_empty(self) -> bool:
        return not any(self.known(f) for f in FIELD_NAMES)


async def load(db: AsyncSession, lead: Lead) -> Requirements:
    row = await db.scalar(select(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
    if row is None:
        return Requirements()
    raw: dict[str, Any] = row.raw or {}
    return Requirements(
        budget_min_inr=row.budget_min_inr,
        budget_max_inr=row.budget_max_inr,
        locations=list(row.locations or []),
        property_type=row.property_type,
        bhk=list(row.bhk or []),
        timeline_months=row.timeline_months,
        loan=raw.get("loan"),
        purpose=row.purpose,
    )


async def save(
    db: AsyncSession, lead: Lead, newer: Requirements, *, source: str
) -> tuple[Requirements, list[str]]:
    """Merge ``newer`` into the lead's requirements and log the change. Flushes."""
    current = await load(db, lead)
    merged, changed = current.merge(newer)
    if not changed:
        return merged, []
    row = await db.scalar(select(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
    if row is None:
        row = LeadRequirement(lead_id=lead.id)
        db.add(row)
    row.budget_min_inr = merged.budget_min_inr
    row.budget_max_inr = merged.budget_max_inr
    row.locations = merged.locations or None
    row.property_type = merged.property_type
    row.bhk = merged.bhk or None
    row.timeline_months = merged.timeline_months
    row.loan_needed = None if merged.loan is None else merged.loan != "not_needed"
    row.purpose = merged.purpose
    row.raw = {**(row.raw or {}), "loan": merged.loan}
    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type="requirements_updated",
            actor_type="system",
            details={"fields": changed, "source": source,
                     "values": merged.model_dump(include=set(changed))},
        )
    )  # fmt: skip
    await db.flush()
    return merged, changed


async def clear_locations(db: AsyncSession, lead: Lead) -> None:
    """The caller said any area is fine: forget the saved areas so searches cover all of them."""
    row = await db.scalar(select(LeadRequirement).where(LeadRequirement.lead_id == lead.id))
    if row is None or not row.locations:
        return
    row.locations = None
    db.add(LeadEvent(lead_id=lead.id, event_type="requirements_updated", actor_type="system",
                     details={"fields": ["locations"], "source": "llm",
                              "values": {"locations": []}}))  # fmt: skip
    await db.flush()
