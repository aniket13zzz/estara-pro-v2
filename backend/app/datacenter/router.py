"""Dashboard API for the Data Center, the Truth Layer, properties and alerts.

Uploading and reading need a signed-in staff user; approving, rejecting and archiving need a
client_admin. Every route depends on ``CurrentUser`` through ``StaffUser``/``AdminUser``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AdminUser, StaffUser
from app.core.config import ClientConfig
from app.core.enums import FactType, ImportBatchStatus
from app.core.settings import Settings
from app.datacenter import service, templates
from app.datacenter.entities import ENTITY_BY_NAME
from app.datacenter.properties import list_properties
from app.datacenter.scanning import UploadRejected
from app.db.deps import get_db
from app.db.models import Alert, ImportBatch, Project
from app.providers.embeddings import Embedder
from app.truth.service import TruthService
from app.truth.staleness import Clock, StalenessPolicy, SystemClock

router = APIRouter(prefix="/api/v1", tags=["datacenter"])
Db = Annotated[AsyncSession, Depends(get_db)]


def _config(request: Request) -> ClientConfig:
    cfg: ClientConfig = request.app.state.client_config
    return cfg


def _clock(request: Request) -> Clock:
    clock: Clock = getattr(request.app.state, "clock", None) or SystemClock()
    return clock


def _policy(cfg: ClientConfig) -> StalenessPolicy:
    return StalenessPolicy(cfg.truth.stale_after_days, cfg.company.timezone)


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ------------------------------------------------------------------------------- models


class BatchOut(BaseModel):
    id: uuid.UUID
    filename: str | None
    entity_type: str
    status: ImportBatchStatus
    row_count: int
    error_count: int
    warning_count: int
    summary: dict[str, Any]
    created_at: datetime
    approved_at: datetime | None
    archived_at: datetime | None
    rejected_reason: str | None

    @classmethod
    def of(cls, b: ImportBatch) -> BatchOut:
        return cls(
            id=b.id,
            filename=b.filename,
            entity_type=b.entity_type,
            status=b.status,
            row_count=b.row_count,
            error_count=b.error_count,
            warning_count=int(b.summary.get("warnings", 0)),
            summary=b.summary,
            created_at=b.created_at,
            approved_at=b.approved_at,
            archived_at=b.archived_at,
            rejected_reason=b.rejected_reason,
        )


class FindingOut(BaseModel):
    entity: str | None
    row_number: int | None
    column: str | None
    severity: str
    code: str
    message: str


class RowOut(BaseModel):
    entity: str
    line: int
    change: str
    changed_fields: list[str]
    valid: bool
    values: dict[str, Any]


class PreviewOut(BaseModel):
    batch: BatchOut
    findings: list[FindingOut]
    rows: list[RowOut]
    total_rows: int
    can_approve: bool


class RejectIn(BaseModel):
    reason: str = Field(default="", max_length=2000)


# ----------------------------------------------------------------------------- templates


@router.get("/datacenter/templates/inventory.xlsx")
async def template_workbook(auth: StaffUser) -> Response:
    return Response(
        templates.inventory_workbook(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="estara-inventory-template.xlsx"'},
    )


@router.get("/datacenter/templates/{entity}.csv")
async def template_csv(entity: str, auth: StaffUser) -> Response:
    if entity not in ENTITY_BY_NAME:
        raise HTTPException(404, "Unknown template")
    return Response(
        templates.entity_csv(entity),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{entity}.csv"'},
    )


# ------------------------------------------------------------------------------- uploads


@router.post("/datacenter/uploads", response_model=BatchOut, status_code=201)
async def upload(
    request: Request,
    auth: StaffUser,
    db: Db,
    file: Annotated[UploadFile, File()],
    entity: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form(max_length=300)] = None,
    doc_type: Annotated[str | None, Form(max_length=32)] = None,
    project_code: Annotated[str | None, Form(max_length=64)] = None,
) -> BatchOut:
    settings: Settings = request.app.state.settings
    limit = max(settings.max_upload_mb_data, settings.max_upload_mb_document) * 1024 * 1024
    data = await file.read(limit + 1)
    embedder: Embedder | None = getattr(request.app.state, "embedder", None)
    try:
        batch = await service.create_batch(
            db,
            settings=settings,
            config=_config(request),
            filename=file.filename or "upload",
            data=data,
            meta=service.UploadMeta(
                entity=entity or None,
                title=title,
                doc_type=doc_type,
                project_code=project_code,
            ),
            user_id=auth.user.id,
            embedder=embedder,
        )
    except UploadRejected as exc:
        await db.rollback()
        raise HTTPException(422, {"code": exc.code, "message": exc.message}) from exc
    return BatchOut.of(batch)


@router.get("/datacenter/batches", response_model=list[BatchOut])
async def list_batches(
    auth: StaffUser, db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[BatchOut]:
    result = await db.execute(
        select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit)
    )
    return [BatchOut.of(b) for b in result.scalars()]


@router.get("/datacenter/batches/{batch_id}", response_model=PreviewOut)
async def preview(
    batch_id: uuid.UUID,
    auth: StaffUser,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PreviewOut:
    try:
        batch = await service.get_batch(db, batch_id)
    except service.BatchNotFound as exc:
        raise HTTPException(404, "Batch not found") from exc
    items = await service.findings(db, batch_id)
    rows, total = await service.rows(db, batch_id, limit=limit, offset=offset)
    return PreviewOut(
        batch=BatchOut.of(batch),
        findings=[
            FindingOut(
                entity=f.entity,
                row_number=f.row_number,
                column=f.column_name,
                severity=f.severity.value,
                code=f.code,
                message=f.message,
            )
            for f in items
        ],
        rows=[
            RowOut(
                entity=r.data["entity"],
                line=r.data["line"],
                change=r.data["change"],
                changed_fields=r.data.get("changed_fields", []),
                valid=r.is_valid,
                values=r.data["values"],
            )
            for r in rows
        ],
        total_rows=total,
        can_approve=batch.status == ImportBatchStatus.VALIDATED,
    )


async def _act(coro: Any) -> BatchOut:
    try:
        batch = await coro
    except service.BatchNotFound as exc:
        raise HTTPException(404, "Batch not found") from exc
    except service.BatchStateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return BatchOut.of(batch)


@router.post("/datacenter/batches/{batch_id}/approve", response_model=BatchOut)
async def approve(batch_id: uuid.UUID, request: Request, auth: AdminUser, db: Db) -> BatchOut:
    return await _act(
        service.approve(
            db, batch_id, user_id=auth.user.id, now=_clock(request).now(), ip_address=_ip(request)
        )
    )


@router.post("/datacenter/batches/{batch_id}/reject", response_model=BatchOut)
async def reject(
    batch_id: uuid.UUID, body: RejectIn, request: Request, auth: AdminUser, db: Db
) -> BatchOut:
    return await _act(
        service.reject(
            db, batch_id, user_id=auth.user.id, reason=body.reason, ip_address=_ip(request)
        )
    )


@router.post("/datacenter/batches/{batch_id}/archive", response_model=BatchOut)
async def archive(batch_id: uuid.UUID, request: Request, auth: AdminUser, db: Db) -> BatchOut:
    return await _act(
        service.archive(
            db, batch_id, user_id=auth.user.id, now=_clock(request).now(), ip_address=_ip(request)
        )
    )


@router.get("/datacenter/freshness")
async def freshness(request: Request, auth: StaffUser, db: Db) -> dict[str, Any]:
    cfg = _config(request)
    now = _clock(request).now()
    alerts = await db.execute(select(Alert).where(Alert.resolved_at.is_(None)))
    return {
        "stale_after_days": cfg.truth.stale_after_days,
        "checked_at": now,
        "entities": await service.freshness(db, _policy(cfg), now),
        "alerts": [_alert(a) for a in alerts.scalars()],
    }


# ----------------------------------------------------------------- properties, truth, alerts


SYNTHETIC_MARK = "synthetic_demo_data"


@router.get("/properties")
async def properties(request: Request, auth: StaffUser, db: Db) -> dict[str, Any]:
    cfg = _config(request)
    now = _clock(request).now()
    synthetic = await db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.description.contains(SYNTHETIC_MARK))
    )
    return {
        "stale_after_days": cfg.truth.stale_after_days,
        "projects": await list_properties(db, _policy(cfg), now),
        # Demo inventory (backend/sample_data) is labelled so it is never mistaken for real stock.
        "synthetic_demo_data": bool(synthetic),
    }


@router.get("/truth/facts")
async def truth_fact(
    request: Request,
    auth: StaffUser,
    db: Db,
    fact_type: FactType,
    project_code: Annotated[str, Query(min_length=1, max_length=64)],
    unit_number: Annotated[str | None, Query(max_length=64)] = None,
    unit_type_code: Annotated[str | None, Query(max_length=64)] = None,
) -> dict[str, Any]:
    truth = TruthService(db, _config(request), _clock(request))
    result = await truth.get(
        fact_type, project_code, unit_number=unit_number, unit_type_code=unit_type_code
    )
    return result.as_dict()


def _alert(a: Alert) -> dict[str, Any]:
    return {
        "id": a.id,
        "kind": a.kind,
        "severity": a.severity.value,
        "message": a.message,
        "details": a.details,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }


@router.get("/alerts")
async def alerts(auth: StaffUser, db: Db) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Alert).where(Alert.resolved_at.is_(None)).order_by(Alert.created_at.desc())
    )
    return [_alert(a) for a in result.scalars()]
