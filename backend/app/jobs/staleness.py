"""Daily staleness check. It only *finds* stale facts and raises (or resolves) a dashboard
alert; staleness itself is computed on every Truth Layer read, so nothing is modified here."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import RecordStatus, Severity
from app.datacenter.entities import ENTITIES
from app.db.models import Alert
from app.truth.staleness import StalenessPolicy

ALERT_KIND = "stale_data"


async def stale_counts(db: AsyncSession, policy: StalenessPolicy, now: datetime) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entity in ENTITIES:
        model = entity.model
        rows = await db.execute(
            select(model.approved_at, model.valid_to).where(model.status == RecordStatus.APPROVED)
        )
        stale = sum(1 for a, v in rows.tuples() if policy.is_stale(a, v, now))
        if stale:
            counts[entity.name] = stale
    return counts


async def run_staleness_check(
    db: AsyncSession, config: ClientConfig, now: datetime
) -> dict[str, Any]:
    """Create/update the open stale-data alert, or resolve it when nothing is stale. Commits."""
    policy = StalenessPolicy(config.truth.stale_after_days, config.company.timezone)
    counts = await stale_counts(db, policy, now)
    alert = await db.scalar(
        select(Alert).where(Alert.kind == ALERT_KIND, Alert.resolved_at.is_(None)).with_for_update()
    )
    total = sum(counts.values())
    if total:
        message = (
            f"{total} approved fact(s) are stale and are not being told to customers. "
            "Upload fresh data and approve it in the Data Center."
        )
        if alert is None:
            alert = Alert(kind=ALERT_KIND, severity=Severity.WARNING, message=message)
            db.add(alert)
        alert.message = message
        alert.details = {"stale": counts, "checked_at": now.isoformat()}
    elif alert is not None:
        alert.resolved_at = now
        alert.details = {**alert.details, "resolved": True, "checked_at": now.isoformat()}
    await db.commit()
    return {"stale": counts, "total": total, "alert_open": bool(total)}
