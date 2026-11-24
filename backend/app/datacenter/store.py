"""Database side of the Data Center: existing-data index, draft writing, approval, archiving.

Versioning: every import writes *new* rows (status ``draft``, version = previous max + 1) tagged
with ``source_batch_id``. Approval archives the previously approved version of each key and
approves the drafts. Rows are never deleted. Foreign keys point at the parent row that was
current when the child was written; lookups resolve parents by code, so later parent versions
still apply.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import RecordStatus
from app.datacenter.entities import ENTITIES, ENTITY_BY_NAME, Entity
from app.datacenter.validation import ExistingIndex, ExistingRow, Key, NormValue, Row, entity_key
from app.db.models import Project, Unit, UnitType


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _area(value: Decimal | None) -> str | None:
    return format(value.quantize(Decimal("0.01")).normalize(), "f") if value is not None else None


def _pct(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def _date(value: NormValue) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) else None


def _dec(value: NormValue) -> Decimal | None:
    return Decimal(value) if isinstance(value, str) else None


@dataclass
class RefMaps:
    project_code: dict[uuid.UUID, str]
    unit_type_code: dict[uuid.UUID, str]
    unit_number: dict[uuid.UUID, str]


async def load_ref_maps(db: AsyncSession) -> RefMaps:
    projects = await db.execute(select(Project.id, Project.code))
    unit_types = await db.execute(select(UnitType.id, UnitType.code))
    units = await db.execute(select(Unit.id, Unit.unit_number))
    return RefMaps(
        project_code=dict(projects.tuples().all()),
        unit_type_code=dict(unit_types.tuples().all()),
        unit_number=dict(units.tuples().all()),
    )


def to_data(entity: str, row: Any, refs: RefMaps) -> dict[str, NormValue]:
    """Normalised data for a database row, in the same shape validation produces."""
    common = {"valid_from": _d(row.valid_from), "valid_to": _d(row.valid_to)}
    if entity == "projects":
        return {
            "project_code": row.code,
            "name": row.name,
            "city": row.city,
            "locality": row.locality,
            "address": row.address,
            "description": row.description,
            "rera_number": row.rera_number,
            "possession_date": _d(row.possession_date),
            **common,
        }
    project_code = refs.project_code.get(row.project_id)
    if entity == "unit_types":
        return {
            "project_code": project_code,
            "unit_type_code": row.code,
            "name": row.name,
            "property_type": row.property_type,
            "bhk": row.bhk,
            "carpet_area_sqft": _area(row.carpet_area_sqft),
            "super_area_sqft": _area(row.super_area_sqft),
            **common,
        }
    if entity == "units":
        return {
            "project_code": project_code,
            "unit_number": row.unit_number,
            "unit_type_code": refs.unit_type_code.get(row.unit_type_id),
            "floor": row.floor,
            "bhk": row.bhk,
            "property_type": row.property_type,
            "carpet_area_sqft": _area(row.carpet_area_sqft),
            "price_inr": row.price_inr,
            "availability": row.availability,
            "facing": row.facing,
            **common,
        }
    if entity == "price_lists":
        return {
            "project_code": project_code,
            "unit_type_code": refs.unit_type_code.get(row.unit_type_id),
            "unit_number": refs.unit_number.get(row.unit_id),
            "base_price_inr": row.base_price_inr,
            "price_max_inr": row.price_max_inr,
            "price_per_sqft_inr": row.price_per_sqft_inr,
            "notes": row.notes,
            **common,
        }
    if entity == "offers":
        return {
            "project_code": project_code,
            "title": row.title,
            "description": row.description,
            "discount_inr": row.discount_inr,
            "discount_percent": _pct(row.discount_percent),
            **common,
        }
    if entity == "payment_plans":
        return {
            "project_code": project_code,
            "name": row.name,
            "description": row.description,
            "milestones": list(row.milestones or []),
            **common,
        }
    if entity == "amenities":
        return {"project_code": project_code, "name": row.name, "category": row.category, **common}
    raise KeyError(entity)


async def load_existing(db: AsyncSession, entities: list[str] | None = None) -> ExistingIndex:
    refs = await load_ref_maps(db)
    index: ExistingIndex = {}
    for entity in ENTITIES:
        if entities is not None and entity.name not in entities:
            continue
        result = await db.execute(select(entity.model))
        by_key: dict[Key, list[ExistingRow]] = defaultdict(list)
        for row in result.scalars():
            data = to_data(entity.name, row, refs)
            by_key[entity_key(entity, data)].append(
                ExistingRow(id=row.id, status=row.status, version=row.version, data=data)
            )
        index[entity.name] = dict(by_key)
    return index


def _referenced_entities(names: set[str]) -> list[str]:
    """Entities whose rows a batch with ``names`` may need (itself plus its references)."""
    needed = set(names)
    for name in names:
        needed |= {r.entity for r in ENTITY_BY_NAME[name].references}
    return [e.name for e in ENTITIES if e.name in needed]


async def load_existing_for(db: AsyncSession, names: set[str]) -> ExistingIndex:
    return await load_existing(db, _referenced_entities(names))


# --------------------------------------------------------------------------- draft writing


def _resolve(
    entity: str, key: Key, batch_ids: dict[str, dict[Key, uuid.UUID]], existing: ExistingIndex
) -> uuid.UUID | None:
    """Row id for a reference: this batch's draft, else approved, else newest draft."""
    if not all(key):
        return None
    if key in batch_ids.get(entity, {}):
        return batch_ids[entity][key]
    rows = [r for r in existing.get(entity, {}).get(key, []) if r.status != RecordStatus.ARCHIVED]
    approved = [r for r in rows if r.status == RecordStatus.APPROVED]
    pick = approved or sorted(rows, key=lambda r: r.version)
    return pick[-1].id if pick else None


def _model_kwargs(
    entity: Entity,
    d: dict[str, NormValue],
    batch_ids: dict[str, dict[Key, uuid.UUID]],
    existing: ExistingIndex,
) -> dict[str, Any]:
    pc = str(d["project_code"])
    common = {"valid_from": _date(d.get("valid_from")), "valid_to": _date(d.get("valid_to"))}
    if entity.name == "projects":
        return {
            "code": pc,
            "name": d["name"],
            "city": d["city"],
            "locality": d.get("locality"),
            "address": d.get("address"),
            "description": d.get("description"),
            "rera_number": d.get("rera_number"),
            "possession_date": _date(d.get("possession_date")),
            **common,
        }
    project_id = _resolve("projects", (pc,), batch_ids, existing)
    ut_key = (pc, str(d.get("unit_type_code") or ""))
    if entity.name == "unit_types":
        return {
            "project_id": project_id,
            "code": d["unit_type_code"],
            "name": d["name"],
            "property_type": d["property_type"],
            "bhk": d["bhk"],
            "carpet_area_sqft": _dec(d.get("carpet_area_sqft")),
            "super_area_sqft": _dec(d.get("super_area_sqft")),
            **common,
        }
    if entity.name == "units":
        return {
            "project_id": project_id,
            "unit_type_id": _resolve("unit_types", ut_key, batch_ids, existing),
            "unit_number": d["unit_number"],
            "floor": d.get("floor"),
            "bhk": d["bhk"],
            "property_type": d["property_type"],
            "carpet_area_sqft": _dec(d.get("carpet_area_sqft")),
            "price_inr": d.get("price_inr"),
            "availability": d["availability"],
            "facing": d.get("facing"),
            **common,
        }
    if entity.name == "price_lists":
        unit_key = (pc, str(d.get("unit_number") or ""))
        return {
            "project_id": project_id,
            "unit_type_id": _resolve("unit_types", ut_key, batch_ids, existing),
            "unit_id": _resolve("units", unit_key, batch_ids, existing),
            "base_price_inr": d["base_price_inr"],
            "price_max_inr": d.get("price_max_inr"),
            "price_per_sqft_inr": d.get("price_per_sqft_inr"),
            "notes": d.get("notes"),
            **common,
        }
    if entity.name == "offers":
        return {
            "project_id": project_id,
            "title": d["title"],
            "description": d.get("description"),
            "discount_inr": d.get("discount_inr"),
            "discount_percent": _dec(d.get("discount_percent")),
            **common,
        }
    if entity.name == "payment_plans":
        return {
            "project_id": project_id,
            "name": d["name"],
            "description": d.get("description"),
            "milestones": d["milestones"],
            **common,
        }
    if entity.name == "amenities":
        return {
            "project_id": project_id,
            "name": d["name"],
            "category": d.get("category"),
            **common,
        }
    raise KeyError(entity.name)


async def write_drafts(
    db: AsyncSession, batch_id: uuid.UUID, rows: list[Row], existing: ExistingIndex
) -> dict[str, int]:
    """Insert one draft row per validated row, parents first. Returns counts per entity."""
    batch_ids: dict[str, dict[Key, uuid.UUID]] = defaultdict(dict)
    counts: dict[str, int] = defaultdict(int)
    for entity in ENTITIES:
        for row in (r for r in rows if r.entity == entity.name):
            versions = [r.version for r in existing.get(entity.name, {}).get(row.key, [])]
            obj = entity.model(
                **_model_kwargs(entity, row.data, batch_ids, existing),
                status=RecordStatus.DRAFT,
                version=max(versions, default=0) + 1,
                source_batch_id=batch_id,
            )
            db.add(obj)
            await db.flush()
            batch_ids[entity.name][row.key] = obj.id
            counts[entity.name] += 1
    return dict(counts)


# --------------------------------------------------------------- approve / reject / archive


async def approve_batch_rows(
    db: AsyncSession, batch_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> dict[str, dict[str, int]]:
    """Approve this batch's drafts; archive the approved versions they replace."""
    index = await load_existing(db)
    result: dict[str, dict[str, int]] = {}
    for entity in ENTITIES:
        model = entity.model
        drafts = (
            (
                await db.execute(
                    select(model).where(
                        model.source_batch_id == batch_id,
                        model.status == RecordStatus.DRAFT,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not drafts:
            continue
        draft_ids = {d.id for d in drafts}
        by_id = {
            r.id: key
            for key, rows in index.get(entity.name, {}).items()
            for r in rows
            if r.id in draft_ids
        }
        replaced: list[uuid.UUID] = []
        for key in set(by_id.values()):
            replaced += [
                r.id
                for r in index[entity.name][key]
                if r.status == RecordStatus.APPROVED and r.id not in draft_ids
            ]
        if replaced:
            await db.execute(
                update(model).where(model.id.in_(replaced)).values(status=RecordStatus.ARCHIVED)
            )
        await db.execute(
            update(model)
            .where(model.id.in_(draft_ids))
            .values(status=RecordStatus.APPROVED, approved_by=user_id, approved_at=now)
        )
        result[entity.name] = {"approved": len(draft_ids), "archived": len(replaced)}
    return result


async def set_batch_rows_status(
    db: AsyncSession, batch_id: uuid.UUID, from_status: RecordStatus, to_status: RecordStatus
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in ENTITIES:
        model = entity.model
        res = await db.execute(
            update(model)
            .where(
                model.source_batch_id == batch_id,
                model.status == from_status,
            )
            .values(status=to_status)
        )
        rowcount = getattr(res, "rowcount", 0) or 0
        if rowcount:
            counts[entity.name] = rowcount
    return counts
