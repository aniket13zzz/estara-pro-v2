"""Shared fixtures.

Database tests need TEST_DATABASE_URL (a PostgreSQL 16 + pgvector database the tests may wipe).
`make check` sets it for the Docker Compose database. Tests do not silently skip without it:
they fail with a clear message, so a missing database can never look like a pass.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ratelimit import MemoryRateLimiter
from app.core.config import ClientConfig, load_config
from app.core.settings import Settings
from app.db import models  # noqa: F401  (registers every table)
from app.db.base import Base
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.truth.staleness import FixedClock

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
CLIENT_DIR = REPO_ROOT / "client"
TEST_SECRET = "test-session-secret-" + "x" * 32
TEST_PASSWORD = "correct horse battery staple"
# Fixed "now" for Data Center and Truth Layer tests (the sample offer is valid on this date).
NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


def get_test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. Start the database (`make up`) and run tests via "
            "`make check`, or export TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/db"
        )
    return url


def run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url}
    return subprocess.run(  # noqa: S603  (fixed command, test-only)
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------- config fixtures


@pytest.fixture
def client_copy(tmp_path: Path) -> Path:
    """A writable copy of client/ so tests can break it."""
    dest = tmp_path / "client"
    shutil.copytree(CLIENT_DIR, dest)
    return dest


@pytest.fixture(scope="session")
def client_config() -> ClientConfig:
    return load_config(CLIENT_DIR / "client.config.yaml")


# ------------------------------------------------------------------------------ db fixtures


@pytest.fixture(scope="session")
def migrated_db() -> Iterator[str]:
    """Reset the test database and migrate it to head once per session."""
    url = get_test_database_url()
    down = run_alembic("downgrade", "base", database_url=url)
    if down.returncode != 0 and "alembic_version" not in down.stderr:
        # Unknown state: drop everything in public and start clean.
        import asyncio

        async def wipe() -> None:
            engine = make_engine(url)
            async with engine.begin() as conn:
                await conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
            await engine.dispose()

        asyncio.run(wipe())
    up = run_alembic("upgrade", "head", database_url=url)
    assert up.returncode == 0, up.stderr
    yield url


def make_test_settings(url: str, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "test",
        "database_url": url,
        "session_secret": TEST_SECRET,
        "cookie_secure": True,
        "dashboard_origin": "https://dashboard.test",
    }
    values.update(overrides)
    return Settings.model_validate(values)


ALL_TABLES_SQL = ", ".join(sorted(Base.metadata.tables))


@pytest.fixture
async def db(migrated_db: str) -> AsyncIterator[AsyncSession]:
    engine = make_engine(migrated_db)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {ALL_TABLES_SQL} CASCADE"))
    factory = make_sessionmaker(engine)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def settings(migrated_db: str, tmp_path: Path) -> Settings:
    return make_test_settings(migrated_db, upload_dir=str(tmp_path / "uploads"))


@pytest.fixture
def app(settings: Settings, client_config: ClientConfig) -> FastAPI:
    """The app under test, with the Data Center clock fixed at NOW.

    Tests may replace ``app.state.clock`` or set ``app.state.embedder``.
    """
    app = create_app(
        config=client_config,
        settings=settings,
        rate_limiter=MemoryRateLimiter(),
        use_redis=False,
    )
    app.state.clock = FixedClock(NOW)
    return app


@pytest.fixture
async def api(app: FastAPI, db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="https://api.test") as client:
            yield client


@pytest.fixture
async def inventory(db: AsyncSession, settings: Settings, client_config: ClientConfig) -> None:
    """Sample users plus the fake sample inventory, imported and approved at NOW through the
    real Data Center pipeline."""
    from app.auth.service import create_user, get_user_by_email
    from app.core.enums import Role
    from app.datacenter import service
    from app.seed import read_inventory

    await create_user(
        db, email="admin@example.com", name="Admin", password=TEST_PASSWORD, role=Role.CLIENT_ADMIN
    )
    await create_user(
        db, email="staff@example.com", name="Staff", password=TEST_PASSWORD, role=Role.SALES_STAFF
    )
    await db.commit()
    admin = await get_user_by_email(db, "admin@example.com")
    assert admin is not None
    folder = BACKEND_DIR / "tests" / "fixtures" / "sample_inventory"
    for entity, filename, content in read_inventory(folder):
        batch = await service.create_batch(
            db,
            settings=settings,
            config=client_config,
            filename=filename,
            data=content,
            meta=service.UploadMeta(entity=entity),
            user_id=admin.id,
        )
        await service.approve(db, batch.id, user_id=admin.id, now=NOW)
