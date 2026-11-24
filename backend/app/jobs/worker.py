"""ARQ worker entry point: ``arq app.jobs.worker.WorkerSettings``.

Jobs:
- ``check_staleness`` — daily at 06:00 in the client's timezone (and once at start-up): finds
  stale facts and opens or resolves the dashboard alert.
- ``send_whatsapp_notices`` — every minute: visit requested / confirmed / reminder notices and
  due follow-ups on WhatsApp (app/jobs/whatsapp_notices.py). Does nothing until WhatsApp is
  configured.
- ``apply_data_retention`` — daily at 03:00: removes old transcripts, message text and recording
  links per ``retention`` in config (app/jobs/retention.py).
- ``heartbeat`` — trivial job to confirm the worker processes jobs.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from arq import cron
from arq.connections import RedisSettings

from app.core.config import load_config
from app.core.logging import configure_logging
from app.core.settings import Settings
from app.db.session import make_engine, make_sessionmaker
from app.jobs.retention import apply_retention
from app.jobs.staleness import run_staleness_check
from app.jobs.whatsapp_notices import send_due_notices
from app.providers.whatsapp import WhatsAppNotConfigured, client_from

log = logging.getLogger("estara.worker")
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
_CONFIG = load_config()  # an invalid config stops the worker at import time


async def heartbeat(ctx: dict[str, Any]) -> str:
    return "ok"


async def check_staleness(ctx: dict[str, Any]) -> dict[str, Any]:
    async with ctx["sessionmaker"]() as db:
        result = await run_staleness_check(db, ctx["config"], datetime.now(UTC))
    log.info("Staleness check: %s stale fact(s)", result["total"])
    return result


async def send_whatsapp_notices(ctx: dict[str, Any]) -> dict[str, int] | None:
    try:
        client = client_from(ctx["settings"])
    except WhatsAppNotConfigured:
        return None
    async with ctx["sessionmaker"]() as db:
        counts = await send_due_notices(db, ctx["config"], client, datetime.now(UTC))
    if any(counts.values()):
        log.info("WhatsApp notices sent: %s", counts)
    return counts


async def apply_data_retention(ctx: dict[str, Any]) -> dict[str, int]:
    async with ctx["sessionmaker"]() as db:
        counts = await apply_retention(db, ctx["config"], datetime.now(UTC))
    log.info("Retention: %s", counts)
    return counts


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    settings = Settings()
    ctx["config"] = _CONFIG
    ctx["settings"] = settings
    ctx["engine"] = make_engine(settings.database_url)
    ctx["sessionmaker"] = make_sessionmaker(ctx["engine"])
    log.info("Worker started for %s", _CONFIG.company.name)


async def shutdown(ctx: dict[str, Any]) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        heartbeat,
        check_staleness,
        send_whatsapp_notices,
        apply_data_retention,
    ]
    cron_jobs: ClassVar[list[Any]] = [
        cron(check_staleness, hour=6, minute=0, run_at_startup=True),
        cron(send_whatsapp_notices, minute=set(range(60)), unique=True, timeout=55),
        cron(apply_data_retention, hour=3, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    timezone = ZoneInfo(_CONFIG.company.timezone)
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", DEFAULT_REDIS_URL))
