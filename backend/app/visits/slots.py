"""Site-visit slots from config: ``site_visits.days``, ``slots``, ``min_lead_hours``,
``max_days_ahead`` and ``slot_capacity`` (visits per project per slot).

Stage 3 needs slots so the agent can offer concrete options and request a visit. The visit
state machine, staff confirmation and rescheduling are Stage 5.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import VisitState
from app.db.models import Project, SiteVisit

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Visits that hold a place in a slot.
HOLDING_STATES = (VisitState.REQUESTED, VisitState.CONFIRMED, VisitState.RESCHEDULED)


@dataclass(frozen=True)
class Slot:
    id: str  # "2026-10-17T11:00", the value request_site_visit takes
    day: date
    at: time
    starts_at: datetime
    remaining: int


def slot_id(day: date, at: time) -> str:
    return f"{day.isoformat()}T{at.strftime('%H:%M')}"


def parse_slot_id(value: str) -> tuple[date, time] | None:
    try:
        day_part, time_part = value.strip().split("T")
        return date.fromisoformat(day_part), time.fromisoformat(time_part)
    except ValueError:
        return None


async def booked_counts(
    db: AsyncSession, project_code: str, start: date, end: date
) -> Counter[tuple[date, time]]:
    result = await db.execute(
        select(SiteVisit.visit_date, SiteVisit.slot)
        .join(Project, Project.id == SiteVisit.project_id)
        .where(
            Project.code == project_code,
            SiteVisit.visit_date >= start,
            SiteVisit.visit_date <= end,
            SiteVisit.state.in_(HOLDING_STATES),
        )
    )
    return Counter(result.tuples().all())


async def available_slots(
    db: AsyncSession,
    config: ClientConfig,
    project_code: str,
    now: datetime,
    *,
    limit: int = 6,
) -> list[Slot]:
    cfg = config.site_visits
    tz = ZoneInfo(config.company.timezone)
    local_now = now.astimezone(tz)
    first_day = local_now.date()
    last_day = first_day + timedelta(days=cfg.max_days_ahead)
    earliest = local_now + timedelta(hours=cfg.min_lead_hours)
    counts = await booked_counts(db, project_code, first_day, last_day)
    times = sorted(time.fromisoformat(s) for s in cfg.slots)
    out: list[Slot] = []
    day = first_day
    while day <= last_day and len(out) < limit:
        if WEEKDAYS[day.weekday()] in cfg.days:
            for at in times:
                starts = datetime.combine(day, at, tz)
                if starts < earliest:
                    continue
                remaining = cfg.slot_capacity - counts[(day, at)]
                if remaining > 0:
                    out.append(Slot(slot_id(day, at), day, at, starts, remaining))
                if len(out) >= limit:
                    break
        day += timedelta(days=1)
    return out
