"""Migrations: upgrade and downgrade on an empty database, and no drift from the models."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from sqlalchemy import text

from app.db.base import Base
from app.db.models import ALL_TABLES
from app.db.session import make_engine
from tests.conftest import get_test_database_url, run_alembic

EXPECTED_TABLES = {
    "users",
    "vapi_tool_calls",
    "sessions",
    "audit_log",
    "uploads",
    "import_batches",
    "import_rows",
    "validation_errors",
    "projects",
    "towers",
    "unit_types",
    "units",
    "price_lists",
    "offers",
    "payment_plans",
    "amenities",
    "documents",
    "document_chunks",
    "customers",
    "customer_identities",
    "conversations",
    "messages",
    "calls",
    "leads",
    "lead_requirements",
    "lead_events",
    "lead_matches",
    "objections",
    "site_visits",
    "site_visit_events",
    "handoffs",
    "follow_ups",
    "webhook_events",
    "alerts",
    "usage_records",
    "agent_state",
}
EMPTY_DB = "estara_migration_test"


def _with_database(url: str, name: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{name}"))


async def _admin(url: str, sql: str) -> None:
    engine = make_engine(url)
    engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(sql))
    await engine.dispose()


async def _scalar(url: str, sql: str) -> object:
    engine = make_engine(url)
    async with engine.connect() as conn:
        value = (await conn.execute(text(sql))).scalar()
    await engine.dispose()
    return value


async def _tables(url: str) -> set[str]:
    engine = make_engine(url)
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
            )
        )
        names = {r[0] for r in rows}
    await engine.dispose()
    return names


@pytest.fixture
def empty_db() -> Iterator[str]:
    base = get_test_database_url()
    asyncio.run(_admin(base, f"DROP DATABASE IF EXISTS {EMPTY_DB} WITH (FORCE)"))
    asyncio.run(_admin(base, f"CREATE DATABASE {EMPTY_DB}"))
    yield _with_database(base, EMPTY_DB)
    asyncio.run(_admin(base, f"DROP DATABASE IF EXISTS {EMPTY_DB} WITH (FORCE)"))


def test_models_declare_every_planned_table() -> None:
    assert set(ALL_TABLES) == EXPECTED_TABLES
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_no_tenant_id_anywhere() -> None:
    for table in Base.metadata.tables.values():
        assert "tenant_id" not in table.columns, table.name


def test_commercial_tables_carry_truth_columns() -> None:
    needed = {
        "status",
        "approved_by",
        "approved_at",
        "valid_from",
        "valid_to",
        "version",
        "source_batch_id",
    }
    for name in (
        "projects",
        "towers",
        "unit_types",
        "units",
        "price_lists",
        "offers",
        "payment_plans",
        "amenities",
    ):
        cols = set(Base.metadata.tables[name].columns.keys())
        assert needed <= cols, f"{name} missing {needed - cols}"
        assert "stale" not in cols and "is_stale" not in cols, "staleness must be computed"


def test_language_columns() -> None:
    tables = Base.metadata.tables
    assert "language" in tables["conversations"].columns
    assert "language" in tables["messages"].columns
    assert "preferred_language" in tables["customers"].columns


def test_upgrade_downgrade_on_empty_database(empty_db: str) -> None:
    up = run_alembic("upgrade", "head", database_url=empty_db)
    assert up.returncode == 0, up.stderr
    assert asyncio.run(_tables(empty_db)) == EXPECTED_TABLES
    assert (
        asyncio.run(
            _scalar(empty_db, "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        )
        is not None
    )

    drift = run_alembic("check", database_url=empty_db)
    assert drift.returncode == 0, f"models and migrations differ:\n{drift.stdout}{drift.stderr}"

    down = run_alembic("downgrade", "base", database_url=empty_db)
    assert down.returncode == 0, down.stderr
    assert asyncio.run(_tables(empty_db)) == set()

    again = run_alembic("upgrade", "head", database_url=empty_db)
    assert again.returncode == 0, again.stderr
    assert asyncio.run(_tables(empty_db)) == EXPECTED_TABLES


def test_approved_rows_need_an_approver(empty_db: str) -> None:
    assert run_alembic("upgrade", "head", database_url=empty_db).returncode == 0
    with pytest.raises(Exception, match="approved_has_approver"):
        asyncio.run(
            _admin(
                empty_db,
                "INSERT INTO projects (code, name, city, status) "
                "VALUES ('P1', 'Sample', 'Kolkata', 'approved')",
            )
        )


def test_status_values_are_constrained(empty_db: str) -> None:
    assert run_alembic("upgrade", "head", database_url=empty_db).returncode == 0
    with pytest.raises(Exception, match="ck_projects_status"):
        asyncio.run(
            _admin(
                empty_db,
                "INSERT INTO projects (code, name, city, status) "
                "VALUES ('P1', 'Sample', 'Kolkata', 'stale')",
            )
        )
