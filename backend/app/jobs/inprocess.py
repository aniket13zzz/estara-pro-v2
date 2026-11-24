"""The worker's daily jobs, run inside the web process (``RUN_JOBS_IN_WEB=true``).

For the free cloud demo there is no Redis and no separate worker (docs/deploy-free.md). This
loop runs, a minute after start and then every few hours:

- the staleness check (dashboard alert when approved facts are getting old);
- data retention (old transcripts and message text are removed per config);
- in demo mode, ``seed --auto`` (reloads the synthetic demo homes before they go stale).

WhatsApp notices are not run here: WhatsApp is not part of the public demo. A client deployment
keeps the ARQ worker (docker-compose.yml), which does all of this plus WhatsApp.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import ClientConfig
from app.core.settings import Settings
from app.jobs.retention import apply_retention
from app.jobs.staleness import run_staleness_check

log = logging.getLogger("estara.jobs")
FIRST_RUN_DELAY_SECONDS = 60
EVERY_SECONDS = 6 * 3600


async def run_once(sessionmaker: async_sessionmaker[AsyncSession], config: ClientConfig,
                   settings: Settings) -> None:  # fmt: skip
    now = datetime.now(UTC)
    if settings.demo_mode:
        from app.seed import run_auto  # imported late: the seed module loads the CLI's deps

        for line in await run_auto():
            log.info("%s", line)
    async with sessionmaker() as db:
        stale = await run_staleness_check(db, config, now)
    async with sessionmaker() as db:
        removed = await apply_retention(db, config, now)
    log.info("In-process jobs: %s stale fact(s), retention %s", stale.get("total"), removed)


async def maintenance_loop(sessionmaker: async_sessionmaker[AsyncSession], config: ClientConfig,
                           settings: Settings) -> None:  # fmt: skip
    await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            await run_once(sessionmaker, config, settings)
        except Exception:  # a failed run must never stop the web process
            log.exception("In-process jobs failed; trying again later")
        await asyncio.sleep(EVERY_SECONDS)
