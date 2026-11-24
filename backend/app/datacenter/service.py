"""Data Center workflow: upload → scan → parse → validate → preview → approve → import.

- Upload: the file is scanned (type from content, size, archive safety) and stored by SHA-256.
- Data files (CSV/XLSX) are parsed and validated. With no errors, every row is written as a
  ``draft`` version tagged with the batch; with errors nothing is written and the batch is
  ``failed`` (the preview lists every problem).
- Documents (PDF/DOCX) are extracted, chunked and embedded as a draft document.
- Approve (client_admin only): drafts become approved, the versions they replace are archived.
- Reject: drafts are archived. Archive: an approved batch's still-current facts are archived.
Every step is audited. Nothing is ever deleted.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record
from app.core.config import ClientConfig
from app.core.enums import ImportBatchStatus, RecordStatus, Severity
from app.core.settings import Settings
from app.datacenter import knowledge, store
from app.datacenter.entities import DOCUMENT, ENTITIES, WORKBOOK
from app.datacenter.scanning import (
    DATA_KINDS,
    UploadRejected,
    detect_kind,
    parse_data_file,
)
from app.datacenter.validation import validate
from app.db.models import (
    Document,
    DocumentChunk,
    ImportBatch,
    ImportRow,
    ImportValidationError,
    Project,
    Upload,
)
from app.providers.embeddings import (
    Embedder,
    EmbeddingsError,
    EmbeddingsNotConfigured,
    get_embedder,
)
from app.truth.staleness import StalenessPolicy

SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class BatchStateError(Exception):
    """The batch is not in a state that allows this action."""


class BatchNotFound(Exception):
    pass


@dataclass(frozen=True)
class UploadMeta:
    entity: str | None = None
    title: str | None = None
    doc_type: str | None = None
    project_code: str | None = None


def safe_filename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    return SAFE_NAME.sub("_", base)[:200] or "upload"


def _store_file(settings: Settings, sha256: str, kind: str, data: bytes) -> str:
    folder = Path(settings.upload_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}.{kind}"
    if not path.exists():
        tmp = path.with_suffix(f".{kind}.tmp-{uuid.uuid4().hex}")
        tmp.write_bytes(data)
        tmp.replace(path)
    return str(path)


async def _project_id_for(db: AsyncSession, code: str) -> uuid.UUID | None:
    rows = await db.execute(
        select(Project.id, Project.status)
        .where(Project.code == code, Project.status != RecordStatus.ARCHIVED)
        .order_by(Project.version.desc())
    )
    found = rows.tuples().all()
    approved = [pid for pid, status in found if status == RecordStatus.APPROVED]
    if approved:
        return approved[0]
    return found[0][0] if found else None


async def create_batch(
    db: AsyncSession,
    *,
    settings: Settings,
    config: ClientConfig,
    filename: str,
    data: bytes,
    meta: UploadMeta,
    user_id: uuid.UUID | None,
    embedder: Embedder | None = None,
) -> ImportBatch:
    """Scan, store, parse and validate an upload. Raises UploadRejected for unusable files."""
    filename = safe_filename(filename)
    hard_limit = max(settings.max_upload_mb_data, settings.max_upload_mb_document) * 1024 * 1024
    if len(data) > hard_limit:
        raise UploadRejected("too_large", "The file is too large.")
    kind = detect_kind(filename, data)
    limit_mb = (
        settings.max_upload_mb_data if kind in DATA_KINDS else settings.max_upload_mb_document
    )
    if len(data) > limit_mb * 1024 * 1024:
        raise UploadRejected("too_large", f"{kind.upper()} files are limited to {limit_mb} MB.")

    sha256 = hashlib.sha256(data).hexdigest()
    upload = Upload(
        filename=filename,
        content_type=kind,
        size_bytes=len(data),
        sha256=sha256,
        storage_path=_store_file(settings, sha256, kind, data),
        kind="data" if kind in DATA_KINDS else "document",
        uploaded_by=user_id,
    )
    db.add(upload)
    await db.flush()

    if kind in DATA_KINDS:
        batch = await _validate_data(db, upload, kind, filename, data, meta, user_id)
    else:
        batch = await _ingest_document(
            db, settings, config, upload, kind, data, meta, user_id, embedder
        )

    await record(
        db,
        "datacenter.upload",
        actor_user_id=user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        details={
            "filename": filename,
            "kind": kind,
            "status": batch.status.value,
            "rows": batch.row_count,
            "errors": batch.error_count,
            "sha256": sha256,
        },
    )
    await db.commit()
    return batch


async def _validate_data(
    db: AsyncSession,
    upload: Upload,
    kind: str,
    filename: str,
    data: bytes,
    meta: UploadMeta,
    user_id: uuid.UUID | None,
) -> ImportBatch:
    parsed = parse_data_file(kind, filename, data, meta.entity)  # type: ignore[arg-type]
    names = {s.entity for s in parsed.sheets}
    existing = await store.load_existing_for(db, names)
    result = validate(parsed, existing)
    errors = result.errors
    batch = ImportBatch(
        upload_id=upload.id,
        entity_type=next(iter(names)) if len(names) == 1 else WORKBOOK,
        status=ImportBatchStatus.FAILED if errors else ImportBatchStatus.VALIDATED,
        row_count=len(result.rows),
        error_count=len(errors),
        created_by=user_id,
        filename=filename,
        summary={"entities": result.summary(), "warnings": len(result.warnings)},
    )
    db.add(batch)
    await db.flush()
    for row in result.rows:
        db.add(
            ImportRow(
                batch_id=batch.id,
                row_number=_row_id(row.entity, row.row_number),
                data={
                    "entity": row.entity,
                    "values": row.data,
                    "change": row.change,
                    "changed_fields": row.changed_fields,
                    "line": row.row_number,
                },
                is_valid=not row.has_error,
            )
        )
    for f in result.findings:
        db.add(
            ImportValidationError(
                batch_id=batch.id,
                row_number=f.row_number,
                column_name=f.column,
                entity=f.entity,
                severity=f.severity,
                code=f.code,
                message=f.message,
            )
        )
    if not errors:
        drafts = await store.write_drafts(db, batch.id, result.rows, existing)
        batch.summary = {**batch.summary, "drafts": drafts}
    await db.flush()
    return batch


def _row_id(entity: str, line: int) -> int:
    """import_rows.row_number is unique per batch; workbooks repeat line numbers per sheet."""
    order = next(i for i, e in enumerate(ENTITIES) if e.name == entity)
    return (order + 1) * 1_000_000 + line


async def _ingest_document(
    db: AsyncSession,
    settings: Settings,
    config: ClientConfig,
    upload: Upload,
    kind: str,
    data: bytes,
    meta: UploadMeta,
    user_id: uuid.UUID | None,
    embedder: Embedder | None,
) -> ImportBatch:
    doc_type = (meta.doc_type or "brochure").strip().lower()
    if doc_type not in knowledge.DOC_TYPES:
        raise UploadRejected(
            "invalid_doc_type", f"Document type must be one of: {', '.join(knowledge.DOC_TYPES)}."
        )
    project_id = None
    if meta.project_code:
        project_id = await _project_id_for(db, meta.project_code.strip())
        if project_id is None:
            raise UploadRejected("unknown_project", f"Project {meta.project_code} does not exist.")
    text = knowledge.extract_text(kind, data)
    try:
        embedder = embedder or get_embedder(config, settings)
        document = await knowledge.ingest_document(
            db,
            embedder,
            upload_id=upload.id,
            title=(meta.title or upload.filename.rsplit(".", 1)[0]).strip()[:300],
            doc_type=doc_type,
            project_id=project_id,
            text=text,
        )
    except EmbeddingsNotConfigured as exc:
        raise UploadRejected(
            "embeddings_not_configured", f"Documents cannot be indexed yet: {exc}."
        ) from exc
    except EmbeddingsError as exc:
        raise UploadRejected("embeddings_failed", f"Indexing the document failed: {exc}.") from exc
    chunks = await db.scalar(
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
    )
    batch = ImportBatch(
        upload_id=upload.id,
        entity_type=DOCUMENT,
        status=ImportBatchStatus.VALIDATED,
        row_count=1,
        error_count=0,
        created_by=user_id,
        filename=upload.filename,
        summary={
            "document_id": str(document.id),
            "title": document.title,
            "doc_type": doc_type,
            "characters": len(text),
            "chunks": int(chunks or 0),
        },
    )
    db.add(batch)
    await db.flush()
    return batch


# ------------------------------------------------------------------------------ actions


async def _locked_batch(db: AsyncSession, batch_id: uuid.UUID) -> ImportBatch:
    batch = await db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update())
    if batch is None:
        raise BatchNotFound(str(batch_id))
    return batch


async def _document_for(db: AsyncSession, batch: ImportBatch) -> Document | None:
    document: Document | None = await db.scalar(
        select(Document).where(Document.upload_id == batch.upload_id)
    )
    return document


async def approve(
    db: AsyncSession,
    batch_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    now: datetime,
    ip_address: str | None = None,
) -> ImportBatch:
    batch = await _locked_batch(db, batch_id)
    if batch.status != ImportBatchStatus.VALIDATED:
        raise BatchStateError(
            f"Only validated batches can be approved (this one is {batch.status})."
        )
    details: dict[str, Any]
    if batch.entity_type == DOCUMENT:
        document = await _document_for(db, batch)
        if document is None:
            raise BatchStateError("The document for this batch is missing.")
        document.status = RecordStatus.APPROVED
        document.approved_by = user_id
        document.approved_at = now
        details = {"document_id": str(document.id)}
    else:
        details = {"entities": await store.approve_batch_rows(db, batch.id, user_id, now)}
    batch.status = ImportBatchStatus.APPROVED
    batch.approved_by = user_id
    batch.approved_at = now
    await record(
        db,
        "datacenter.batch_approved",
        actor_user_id=user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        details=details,
        ip_address=ip_address,
    )
    await db.commit()
    return batch


async def reject(
    db: AsyncSession,
    batch_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    reason: str,
    ip_address: str | None = None,
) -> ImportBatch:
    batch = await _locked_batch(db, batch_id)
    if batch.status not in (ImportBatchStatus.VALIDATED, ImportBatchStatus.FAILED):
        raise BatchStateError(f"Only pending batches can be rejected (this one is {batch.status}).")
    if batch.entity_type == DOCUMENT:
        document = await _document_for(db, batch)
        if document is not None:
            document.status = RecordStatus.ARCHIVED
        counts: dict[str, int] = {"documents": 1 if document else 0}
    else:
        counts = await store.set_batch_rows_status(
            db, batch.id, RecordStatus.DRAFT, RecordStatus.ARCHIVED
        )
    batch.status = ImportBatchStatus.REJECTED
    batch.rejected_reason = reason.strip()[:2000] or None
    await record(
        db,
        "datacenter.batch_rejected",
        actor_user_id=user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        details={"reason": batch.rejected_reason, "archived_drafts": counts},
        ip_address=ip_address,
    )
    await db.commit()
    return batch


async def archive(
    db: AsyncSession,
    batch_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    now: datetime,
    ip_address: str | None = None,
) -> ImportBatch:
    batch = await _locked_batch(db, batch_id)
    if batch.status != ImportBatchStatus.APPROVED:
        raise BatchStateError(
            f"Only approved batches can be archived (this one is {batch.status})."
        )
    if batch.entity_type == DOCUMENT:
        document = await _document_for(db, batch)
        if document is not None:
            document.status = RecordStatus.ARCHIVED
        counts: dict[str, int] = {"documents": 1 if document else 0}
    else:
        counts = await store.set_batch_rows_status(
            db, batch.id, RecordStatus.APPROVED, RecordStatus.ARCHIVED
        )
    batch.status = ImportBatchStatus.ARCHIVED
    batch.archived_at = now
    await record(
        db,
        "datacenter.batch_archived",
        actor_user_id=user_id,
        entity_type="import_batch",
        entity_id=batch.id,
        details={"archived": counts},
        ip_address=ip_address,
    )
    await db.commit()
    return batch


# ------------------------------------------------------------------------------- reading


async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> ImportBatch:
    batch = await db.get(ImportBatch, batch_id)
    if batch is None:
        raise BatchNotFound(str(batch_id))
    return batch


async def findings(db: AsyncSession, batch_id: uuid.UUID) -> list[ImportValidationError]:
    result = await db.execute(
        select(ImportValidationError)
        .where(ImportValidationError.batch_id == batch_id)
        .order_by(
            case(
                (ImportValidationError.severity == Severity.ERROR, 0),
                (ImportValidationError.severity == Severity.WARNING, 1),
                else_=2,
            ),
            ImportValidationError.entity,
            ImportValidationError.row_number,
        )
    )
    return list(result.scalars())


async def rows(
    db: AsyncSession, batch_id: uuid.UUID, *, limit: int, offset: int
) -> tuple[list[ImportRow], int]:
    total = await db.scalar(
        select(func.count()).select_from(ImportRow).where(ImportRow.batch_id == batch_id)
    )
    result = await db.execute(
        select(ImportRow)
        .where(ImportRow.batch_id == batch_id)
        .order_by(ImportRow.row_number)
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars()), int(total or 0)


async def freshness(
    db: AsyncSession, policy: StalenessPolicy, now: datetime
) -> list[dict[str, Any]]:
    """Per data type: approved rows, how many are stale, oldest approval, next to go stale."""
    out = []
    for entity in ENTITIES:
        model = entity.model
        result = await db.execute(
            select(model.approved_at, model.valid_to).where(model.status == RecordStatus.APPROVED)
        )
        pairs = result.tuples().all()
        stale = [p for p in pairs if policy.is_stale(p[0], p[1], now)]
        fresh = [p for p in pairs if not policy.is_stale(p[0], p[1], now)]
        drafts = await db.scalar(
            select(func.count()).select_from(model).where(model.status == RecordStatus.DRAFT)
        )
        next_stale = min((policy.stale_from(a, v) for a, v in fresh if a), default=None)
        out.append(
            {
                "entity": entity.name,
                "label": entity.label,
                "approved": len(pairs),
                "stale": len(stale),
                "pending": int(drafts or 0),
                "oldest_approved_at": min((a for a, _ in pairs if a), default=None),
                "next_stale_at": next_stale,
            }
        )
    return out


def has_errors(items: list[ImportValidationError]) -> bool:
    return any(i.severity == Severity.ERROR for i in items)
