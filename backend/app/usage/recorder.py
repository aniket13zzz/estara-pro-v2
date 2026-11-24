"""Usage recording. LLM token counts come from each provider's response; cost is computed from
``llm.pricing`` in the client config (providers return tokens, not prices)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.core.enums import UsageKind
from app.db.models import UsageRecord
from app.providers.llm import ChatResult, UsageRecorder, cost_usd


def llm_usage_recorder(
    db: AsyncSession, config: ClientConfig, *, conversation_id: uuid.UUID | None
) -> UsageRecorder:
    async def record(result: ChatResult, purpose: str) -> None:
        cost = cost_usd(config, result.usage)
        db.add(
            UsageRecord(
                kind=UsageKind.LLM_TOKENS,
                provider=config.llm.provider,
                quantity=Decimal(result.usage.total_tokens),
                unit="tokens",
                cost_usd=Decimal(str(round(cost, 6))) if cost is not None else None,
                conversation_id=conversation_id,
                details={
                    "model": result.model or config.llm.model,
                    "purpose": purpose,
                    "input_tokens": result.usage.input_tokens,
                    "output_tokens": result.usage.output_tokens,
                    "latency_ms": result.latency_ms,
                    "finish_reason": result.finish_reason,
                },
            )
        )
        await db.flush()

    return record
