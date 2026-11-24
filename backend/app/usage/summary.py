"""This month's usage against the limits in ``usage_limits`` (client timezone months).

Counted from our own records: call minutes from ``calls.duration_seconds`` (Vapi's end-of-call
report), AI requests and cost from ``usage_records`` (every LLM call), WhatsApp messages from
outbound messages WhatsApp accepted. Costs are estimates from ``llm.pricing`` in config; the
provider's own bill is the final word.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import Channel, MessageDirection, UsageKind
from app.db.models import Call, Message, UsageRecord


def month_start(config: ClientConfig, now: datetime) -> datetime:
    local = now.astimezone(ZoneInfo(config.company.timezone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass
class MonthUsage:
    since: str
    calls: int
    call_minutes: float
    call_minutes_limit: int
    ai_requests: int
    ai_cost_usd: float
    ai_cost_limit_usd: float
    whatsapp_messages: int
    warnings: list[str] = field(default_factory=list)
    call_limit_reached: bool = False
    ai_limit_reached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _warn(label: str, used: float, limit: float, steps: list[int]) -> str | None:
    if limit <= 0:
        return None
    percent = 100 * used / limit
    crossed = [s for s in sorted(steps) if percent >= s]
    if not crossed:
        return None
    return f"{label}: {percent:.0f}% of this month's limit used"


async def month_usage(db: AsyncSession, config: ClientConfig, now: datetime) -> MonthUsage:
    since = month_start(config, now)
    calls, seconds = (await db.execute(
        select(func.count(Call.id), func.coalesce(func.sum(Call.duration_seconds), 0))
        .where(Call.started_at >= since)
    )).one()  # fmt: skip
    requests, cost = (await db.execute(
        select(func.count(UsageRecord.id), func.coalesce(func.sum(UsageRecord.cost_usd), 0))
        .where(UsageRecord.kind == UsageKind.LLM_TOKENS, UsageRecord.occurred_at >= since)
    )).one()  # fmt: skip
    whatsapp = await db.scalar(
        select(func.count(Message.id)).where(
            Message.channel == Channel.WHATSAPP, Message.direction == MessageDirection.OUTBOUND,
            Message.provider_message_id.is_not(None), Message.created_at >= since,
        )
    )  # fmt: skip
    limits = config.usage_limits
    minutes = round(int(seconds) / 60, 1)
    ai_cost = float(Decimal(cost or 0))
    usage = MonthUsage(
        since=since.date().isoformat(), calls=int(calls), call_minutes=minutes,
        call_minutes_limit=limits.monthly_call_minutes, ai_requests=int(requests),
        ai_cost_usd=round(ai_cost, 4), ai_cost_limit_usd=limits.monthly_llm_usd,
        whatsapp_messages=int(whatsapp or 0),
        call_limit_reached=minutes >= limits.monthly_call_minutes,
        ai_limit_reached=ai_cost >= limits.monthly_llm_usd,
    )  # fmt: skip
    for label, used, limit in (("Call minutes", minutes, limits.monthly_call_minutes),
                               ("AI cost", ai_cost, limits.monthly_llm_usd)):  # fmt: skip
        warning = _warn(label, used, limit, limits.alert_at_percent)
        if warning:
            usage.warnings.append(warning)
    return usage
