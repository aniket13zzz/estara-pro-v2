"""Computed staleness. Never stored as a status (CLAUDE.md, ADR 0005).

A fact row is stale when it was approved more than ``truth.stale_after_days`` ago, or its
``valid_to`` date has passed. Re-approving (re-importing) the row gives it a new ``approved_at``
and so clears staleness automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=ZoneInfo("UTC"))


@dataclass(frozen=True)
class FixedClock:
    at: datetime

    def now(self) -> datetime:
        return self.at


@dataclass(frozen=True)
class StalenessPolicy:
    stale_after_days: int
    timezone: str

    def today(self, now: datetime) -> date:
        """Today's date where the client is (validity dates are client-local)."""
        return now.astimezone(ZoneInfo(self.timezone)).date()

    def is_stale(self, approved_at: datetime | None, valid_to: date | None, now: datetime) -> bool:
        if approved_at is None:
            return True
        if now - approved_at > timedelta(days=self.stale_after_days):
            return True
        return valid_to is not None and valid_to < self.today(now)

    def stale_reason(
        self, approved_at: datetime | None, valid_to: date | None, now: datetime
    ) -> str | None:
        if approved_at is None:
            return "never approved"
        if now - approved_at > timedelta(days=self.stale_after_days):
            return f"approved more than {self.stale_after_days} days ago"
        if valid_to is not None and valid_to < self.today(now):
            return f"valid only until {valid_to.isoformat()}"
        return None

    def stale_from(self, approved_at: datetime, valid_to: date | None) -> datetime:
        """When a row becomes stale if nothing changes."""
        by_age = approved_at + timedelta(days=self.stale_after_days)
        if valid_to is None:
            return by_age
        tz = ZoneInfo(self.timezone)
        by_validity = datetime.combine(valid_to + timedelta(days=1), datetime.min.time(), tz)
        return min(by_age, by_validity)

    def not_yet_valid(self, valid_from: date | None, now: datetime) -> bool:
        return valid_from is not None and valid_from > self.today(now)
