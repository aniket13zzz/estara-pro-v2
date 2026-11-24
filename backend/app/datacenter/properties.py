"""Properties overview for the dashboard: approved projects with unit types, unit counts,
last update and stale warnings, plus projects waiting for approval."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordStatus
from app.db.models import PriceList, Project, Unit, UnitType
from app.truth import formatters as fmt
from app.truth.staleness import StalenessPolicy


def _freshness(policy: StalenessPolicy, row: Any, now: datetime) -> dict[str, Any]:
    reason = policy.stale_reason(row.approved_at, row.valid_to, now)
    return {"approved_at": row.approved_at, "stale": reason is not None, "stale_reason": reason}


async def list_properties(
    db: AsyncSession, policy: StalenessPolicy, now: datetime
) -> list[dict[str, Any]]:
    projects = (await db.execute(select(Project))).scalars().all()
    code_of = {p.id: p.code for p in projects}
    by_code: dict[str, list[Project]] = defaultdict(list)
    for p in projects:
        by_code[p.code].append(p)

    def approved(model: Any) -> Any:
        return select(model).where(model.status == RecordStatus.APPROVED)

    unit_types = (await db.execute(approved(UnitType))).scalars().all()
    units = (await db.execute(approved(Unit))).scalars().all()
    price_lists = (await db.execute(approved(PriceList))).scalars().all()
    ut_code = {u.id: u.code for u in (await db.execute(select(UnitType))).scalars()}

    out: list[dict[str, Any]] = []
    for code in sorted(by_code):
        rows = by_code[code]
        current = [p for p in rows if p.status == RecordStatus.APPROVED]
        drafts = [p for p in rows if p.status == RecordStatus.DRAFT]
        if not current and not drafts:
            continue  # archived only
        if not current:
            latest = max(drafts, key=lambda p: p.version)
            out.append(
                {
                    "code": code,
                    "name": latest.name,
                    "city": latest.city,
                    "locality": latest.locality,
                    "status": "pending",
                    "pending_versions": len(drafts),
                    "unit_types": [],
                    "units": {"total": 0, "available": 0, "stale": 0},
                }
            )
            continue
        project = current[0]
        types = [u for u in unit_types if code_of.get(u.project_id) == code]
        project_units = [u for u in units if code_of.get(u.project_id) == code]
        lists = [p for p in price_lists if code_of.get(p.project_id) == code]
        type_rows = []
        for ut in sorted(types, key=lambda t: (t.bhk, t.code)):
            prices = [
                v
                for p in lists
                if p.unit_id is None and ut_code.get(p.unit_type_id) == ut.code
                for v in (p.base_price_inr, p.price_max_inr or p.base_price_inr)
            ]
            members = [u for u in project_units if ut_code.get(u.unit_type_id) == ut.code]
            type_rows.append(
                {
                    "code": ut.code,
                    "name": ut.name,
                    "bhk": ut.bhk,
                    "property_type": ut.property_type,
                    "carpet_area": fmt.display_area(ut.carpet_area_sqft)
                    if ut.carpet_area_sqft
                    else None,
                    "price": (
                        fmt.display_inr_range(min(prices), max(prices))
                        if prices and min(prices) != max(prices)
                        else fmt.display_inr(prices[0])
                        if prices
                        else None
                    ),
                    "units": len(members),
                    "available": sum(1 for u in members if u.availability == "available"),
                    **_freshness(policy, ut, now),
                }
            )
        stale_units = sum(
            1 for u in project_units if policy.is_stale(u.approved_at, u.valid_to, now)
        )
        out.append(
            {
                "code": code,
                "name": project.name,
                "city": project.city,
                "locality": project.locality,
                "status": "approved",
                "pending_versions": len(drafts),
                "possession": fmt.display_month_year(project.possession_date)
                if project.possession_date
                else None,
                "version": project.version,
                **_freshness(policy, project, now),
                "unit_types": type_rows,
                "units": {
                    "total": len(project_units),
                    "available": sum(1 for u in project_units if u.availability == "available"),
                    "stale": stale_units,
                },
            }
        )
    return out
