"""The free cloud demo (docs/deploy-free.md): hosted Postgres URLs, no Redis, jobs in the web
process, demo data in production only with DEMO_MODE, short demo calls, admin diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ratelimit import MemoryRateLimiter
from app.channels.vapi.native import native_update
from app.core.config import ClientConfig
from app.core.settings import Settings
from app.jobs.inprocess import run_once
from app.main import create_app
from app.seed import auto_decision
from tests.conftest import make_test_settings
from tests.helpers import ADMIN, login, make_users
from tests.integration.test_native_voice import _existing
from tests.integration.test_voice import voice_settings


def test_hosted_postgres_urls_get_the_psycopg_driver(migrated_db: str) -> None:
    for given in ("postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
                  "postgres://u:p@ep-x.neon.tech/db?sslmode=require"):  # fmt: skip
        url = make_test_settings(migrated_db, database_url=given).database_url
        assert url == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
    assert make_test_settings(migrated_db).database_url == migrated_db  # already right


async def test_without_redis_the_app_runs_and_is_healthy(
    migrated_db: str, client_config: ClientConfig, tmp_path: Path
) -> None:
    settings = make_test_settings(migrated_db, redis_enabled=False,
                                  upload_dir=str(tmp_path))  # fmt: skip
    app = create_app(config=client_config, settings=settings)  # use_redis left at its default
    async with app.router.lifespan_context(app):
        assert app.state.redis is None
        assert isinstance(app.state.rate_limiter, MemoryRateLimiter)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://a.test") as c:
            body = (await c.get("/api/v1/health")).json()
    assert body["checks"]["redis"] == "not_configured" and body["checks"]["database"] == "ok"


async def test_jobs_run_in_the_web_process(
    db: AsyncSession, settings: Settings, client_config: ClientConfig, app: object
) -> None:
    from app.db.session import make_engine, make_sessionmaker

    engine = make_engine(settings.database_url)
    try:
        await run_once(make_sessionmaker(engine), client_config, settings)  # no error, no Redis
    finally:
        await engine.dispose()


async def test_demo_data_loads_in_production_only_in_demo_mode(
    db: AsyncSession, settings: Settings
) -> None:
    now = datetime.now(UTC)
    live = settings.model_copy(update={"environment": "production"})
    assert "production" in (await auto_decision(db, live, now) or "")
    demo = live.model_copy(update={"demo_mode": True})
    assert await auto_decision(db, demo, now) is None  # empty demo database: load


def test_demo_calls_are_kept_short(client_config: ClientConfig, migrated_db: str) -> None:
    normal = voice_settings(migrated_db, vapi_assistant_id="a")
    demo = voice_settings(migrated_db, vapi_assistant_id="a", demo_mode=True,
                          demo_max_call_minutes=5)  # fmt: skip
    long = native_update(_existing(), client_config, normal)["maxDurationSeconds"]
    short = native_update(_existing(), client_config, demo)["maxDurationSeconds"]
    assert long == client_config.voice.max_call_minutes * 60 and short == 300


async def test_diagnostics_are_for_admins_only(api: AsyncClient, db: AsyncSession) -> None:
    assert (await api.get("/api/v1/diagnostics")).status_code == 401
    await make_users(db)
    await login(api, ADMIN)
    r = await api.get("/api/v1/diagnostics")
    assert r.status_code == 200
    report = r.json()["report"]
    assert "ESTARA demo check" in report and "PUBLIC_BASE_URL" in report  # not set in tests
    assert "session" not in report.lower() and "secret" not in report.lower()
