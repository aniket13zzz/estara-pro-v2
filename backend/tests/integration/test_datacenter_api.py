"""Data Center through the HTTP API: upload → validate → preview → approve, versioning,
permissions, rejection, archiving, templates, freshness and properties."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import RecordStatus
from app.core.settings import Settings
from app.db.models import AuditLog, ImportBatch, Project, Unit
from app.truth.staleness import FixedClock
from tests.helpers import (
    ADMIN,
    SAMPLE_DIR,
    STAFF,
    approve,
    csv_bytes,
    import_and_approve,
    later,
    load_sample,
    login,
    make_users,
    sample_csv,
    sample_workbook,
    upload,
    upload_ok,
)

UNIT_HEADER = (
    "project_code,unit_number,unit_type_code,floor,bhk,property_type,carpet_area_sqft,"
    "price_inr,availability,facing,valid_from,valid_to"
)


async def _audit(db: AsyncSession) -> list[str]:
    db.expire_all()
    return list((await db.execute(select(AuditLog.action))).scalars())


async def _fact(api: AsyncClient, **params: str) -> dict[str, Any]:
    r = await api.get("/api/v1/truth/facts", params=params)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


# ------------------------------------------------------------------------- end to end


async def test_sample_inventory_imports_end_to_end(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await load_sample(api, h)

    projects = (await api.get("/api/v1/properties")).json()["projects"]
    assert [p["code"] for p in projects] == ["SG", "SH", "SL"]
    sg = projects[0]
    assert sg["status"] == "approved" and sg["possession"] == "December 2027"
    assert sg["units"] == {"total": 7, "available": 5, "stale": 0}
    two_bhk = next(t for t in sg["unit_types"] if t["code"] == "2BHK-A")
    assert two_bhk["price"] == "₹72,00,000 – ₹78,00,000"

    fact = await _fact(api, fact_type="price", project_code="SG", unit_number="A-1203")
    assert fact["status"] == "available"
    assert fact["spoken"] == {"en": "72 lakh rupees", "hi": "बहत्तर लाख रुपये"}
    assert fact["source"][0]["table"] == "units"

    actions = await _audit(db)
    assert actions.count("datacenter.upload") == 7
    assert actions.count("datacenter.batch_approved") == 7


async def test_workbook_upload_in_one_batch(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    batch = await upload_ok(api, h, "inventory.xlsx", sample_workbook({"Notes": [["x"]]}))
    assert batch["entity_type"] == "inventory"
    assert batch["status"] == "validated"
    assert batch["row_count"] == 3 + 7 + 14 + 7 + 1 + 4 + 7
    preview = (await api.get(f"/api/v1/datacenter/batches/{batch['id']}")).json()
    assert {f["code"] for f in preview["findings"]} == {"unknown_sheet"}
    assert preview["can_approve"] is True
    assert (await approve(api, h, batch["id"])).status_code == 200
    fact = await _fact(api, fact_type="possession", project_code="SL")
    assert fact["spoken"]["hi"] == "जून 2028"


async def test_preview_lists_row_errors_and_blocks_approval(
    api: AsyncClient, db: AsyncSession
) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await import_and_approve(api, h, "projects.csv", sample_csv("projects"))
    bad = csv_bytes(
        UNIT_HEADER,
        "SG,A-1,,3,2,apartment,820,0,available,,,",
        "SG,A-2,,3,2,apartment,820,7200000,available,,,",
        "SG,A-2,,3,2,apartment,820,7500000,available,,,",
        "ZZ,A-3,,3,2,apartment,820,7200000,available,,,",
    )
    batch = await upload_ok(api, h, "units.csv", bad)
    assert batch["status"] == "failed" and batch["error_count"] == 3
    preview = (await api.get(f"/api/v1/datacenter/batches/{batch['id']}")).json()
    by_code = {f["code"]: f for f in preview["findings"]}
    assert by_code["price_not_positive"]["row_number"] == 2
    assert by_code["contradiction"]["row_number"] == 4
    assert by_code["unknown_project"]["row_number"] == 5
    assert preview["can_approve"] is False
    assert len(preview["rows"]) == 4

    r = await approve(api, h, batch["id"])
    assert r.status_code == 409
    # Nothing was written for a failed batch.
    assert await db.scalar(select(func.count()).select_from(Unit)) == 0


async def test_upload_rejections_are_clear(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    r = await upload(api, h, "units.pdf", b"a,b\n1,2\n")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "extension_mismatch"
    r = await upload(api, h, "units.xlsm", b"PK\x03\x04junk")
    assert r.status_code == 422


# ------------------------------------------------------------------------- versioning


async def test_reimport_creates_new_version_and_archives_old(
    api: AsyncClient, db: AsyncSession
) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await load_sample(api, h)
    first = await db.scalar(
        select(Unit).where(Unit.unit_number == "A-1203", Unit.status == RecordStatus.APPROVED)
    )
    assert first is not None and first.version == 1

    changed = sample_csv("units").replace(
        b"SG,A-1203,2BHK-A,12,2,apartment,820,7200000",
        b"SG,A-1203,2BHK-A,12,2,apartment,820,7300000",
    )
    batch = await upload_ok(api, h, "units.csv", changed)
    assert batch["summary"]["entities"]["units"]["changed"] == 1
    assert batch["summary"]["entities"]["units"]["unchanged"] == 13

    # Before approval the old price is still the truth.
    assert (await _fact(api, fact_type="price", project_code="SG", unit_number="A-1203"))["value"][
        "min"
    ] == 7_200_000
    assert (await approve(api, h, batch["id"])).status_code == 200
    fact = await _fact(api, fact_type="price", project_code="SG", unit_number="A-1203")
    assert fact["value"] == {"min": 7_300_000, "max": 7_300_000}
    assert fact["source"][0]["version"] == 2

    db.expire_all()
    versions = (
        (
            await db.execute(
                select(Unit.version, Unit.status)
                .where(Unit.unit_number == "A-1203")
                .order_by(Unit.version)
            )
        )
        .tuples()
        .all()
    )
    assert versions == [(1, RecordStatus.ARCHIVED), (2, RecordStatus.APPROVED)]
    # Every fact keeps its source batch.
    batch_ids = set((await db.execute(select(Unit.source_batch_id))).scalars())
    assert None not in batch_ids and len(batch_ids) == 2


async def test_new_project_version_keeps_children_linked(
    api: AsyncClient, db: AsyncSession
) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await load_sample(api, h)
    renamed = sample_csv("projects").replace(b"Sunrise Greens (Sample)", b"Sunrise Greens II")
    await import_and_approve(api, h, "projects.csv", renamed)
    fact = await _fact(api, fact_type="price", project_code="SG", unit_type_code="3BHK")
    assert fact["status"] == "available"
    projects = (await api.get("/api/v1/properties")).json()["projects"]
    sg = next(p for p in projects if p["code"] == "SG")
    assert sg["name"] == "Sunrise Greens II" and sg["version"] == 2
    assert sg["units"]["total"] == 7


# ------------------------------------------------------------------ approval permissions


async def test_only_client_admin_can_approve(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    staff = await login(api, STAFF)
    batch = await upload_ok(api, staff, "projects.csv", sample_csv("projects"))
    r = await approve(api, staff, batch["id"])
    assert r.status_code == 403
    r = await api.post(
        f"/api/v1/datacenter/batches/{batch['id']}/reject", json={"reason": "x"}, headers=staff
    )
    assert r.status_code == 403

    admin = await login(api, ADMIN)
    assert (await approve(api, admin, batch["id"])).status_code == 200
    db.expire_all()
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "datacenter.batch_approved"))
    assert entry is not None and entry.details["entities"]["projects"]["approved"] == 3


async def test_cannot_approve_twice(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    batch = await import_and_approve(api, h, "projects.csv", sample_csv("projects"))
    assert (await approve(api, h, batch["id"])).status_code == 409


async def test_reject_archives_drafts(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    batch = await upload_ok(api, h, "projects.csv", sample_csv("projects"))
    fact = await _fact(api, fact_type="possession", project_code="SG")
    assert (fact["status"], fact["reason"]) == ("unavailable", "unapproved")

    r = await api.post(
        f"/api/v1/datacenter/batches/{batch['id']}/reject",
        json={"reason": "wrong dates"},
        headers=h,
    )
    assert r.status_code == 200 and r.json()["rejected_reason"] == "wrong dates"
    db.expire_all()
    statuses = set((await db.execute(select(Project.status))).scalars())
    assert statuses == {RecordStatus.ARCHIVED}  # kept, not deleted
    assert (await _fact(api, fact_type="possession", project_code="SG"))["reason"] == "missing"
    assert "datacenter.batch_rejected" in await _audit(db)


async def test_archive_withdraws_an_approved_batch(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await import_and_approve(api, h, "projects.csv", sample_csv("projects"))
    offers = await import_and_approve(api, h, "offers.csv", sample_csv("offers"))
    r = await api.post(f"/api/v1/datacenter/batches/{offers['id']}/archive", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "archived"
    fact = await _fact(api, fact_type="offer", project_code="SG")
    assert fact["reason"] == "missing"
    assert "datacenter.batch_archived" in await _audit(db)


async def test_batches_list(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await upload_ok(api, h, "projects.csv", sample_csv("projects"))
    rows = (await api.get("/api/v1/datacenter/batches")).json()
    assert len(rows) == 1 and rows[0]["filename"] == "projects.csv"
    assert await db.scalar(select(func.count()).select_from(ImportBatch)) == 1


# ------------------------------------------------------------------- templates, freshness


async def test_template_downloads(api: AsyncClient, db: AsyncSession) -> None:
    await make_users(db)
    await login(api, STAFF)
    r = await api.get("/api/v1/datacenter/templates/units.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.decode("utf-8-sig").startswith("project_code,unit_number")
    r = await api.get("/api/v1/datacenter/templates/inventory.xlsx")
    assert r.status_code == 200 and r.content.startswith(b"PK")
    assert (await api.get("/api/v1/datacenter/templates/nope.csv")).status_code == 404


async def test_freshness_reports_stale_rows(
    api: AsyncClient, db: AsyncSession, app: FastAPI
) -> None:
    await make_users(db)
    h = await login(api, ADMIN)
    await load_sample(api, h)
    body = (await api.get("/api/v1/datacenter/freshness")).json()
    units = next(e for e in body["entities"] if e["entity"] == "units")
    assert (units["approved"], units["stale"], units["pending"]) == (14, 0, 0)
    assert units["next_stale_at"] is not None

    app.state.clock = FixedClock(later(400))
    body = (await api.get("/api/v1/datacenter/freshness")).json()
    assert all(e["stale"] == e["approved"] for e in body["entities"])
    projects = (await api.get("/api/v1/properties")).json()["projects"]
    assert projects[0]["stale"] is True and projects[0]["units"]["stale"] == 7


async def test_seed_loads_sample_inventory_through_the_pipeline(
    db: AsyncSession, settings: Settings, client_config: ClientConfig
) -> None:
    from app.auth.service import get_user_by_email
    from app.seed import seed_inventory

    await make_users(db)
    admin = await get_user_by_email(db, ADMIN)
    assert admin is not None
    messages = await seed_inventory(
        db, settings=settings, config=client_config, admin=admin, folder=SAMPLE_DIR
    )
    assert len(messages) == 7 and "units.csv: 14 rows (14 new" in messages[2]
    again = await seed_inventory(
        db, settings=settings, config=client_config, admin=admin, folder=SAMPLE_DIR
    )
    assert "14 unchanged" in again[2]
    db.expire_all()
    approved = await db.scalar(
        select(func.count()).select_from(Unit).where(Unit.status == RecordStatus.APPROVED)
    )
    assert approved == 14
