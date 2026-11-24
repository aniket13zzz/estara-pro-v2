"""Data retention (``retention`` in client.config.yaml), run daily by the worker.

- Call transcripts and summaries, and the text of conversation messages, older than
  ``retention.transcript_days`` are replaced with a short notice. Leads, requirements, visits
  and handoffs stay, so the sales history is kept without the personal conversation text.
- Native voice tool calls (their arguments and answers hold names and budgets) older than
  ``retention.transcript_days`` are deleted.
- Recording links older than ``retention.recording_days`` are removed. The recordings themselves
  live with Vapi; delete them there too (docs/deploy.md). This job never deletes Vapi data.

The legal retention periods are the client's decision (docs/before-first-customer.md).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.db.models import Call, Message, VapiToolCall

REMOVED = "[removed after the retention period]"


async def apply_retention(db: AsyncSession, config: ClientConfig, now: datetime) -> dict[str, int]:
    text_before = now - timedelta(days=config.retention.transcript_days)
    recording_before = now - timedelta(days=config.retention.recording_days)
    transcripts = await db.execute(
        update(Call)
        .where(Call.created_at < text_before, Call.transcript.is_not(None),
               Call.transcript != REMOVED)
        .values(transcript=REMOVED, summary_en=None, raw_report=None)
    )  # fmt: skip
    messages = await db.execute(
        update(Message)
        .where(Message.created_at < text_before, Message.content != REMOVED)
        .values(content=REMOVED, tool_calls=None, meta={})
    )  # fmt: skip
    tool_calls = await db.execute(delete(VapiToolCall).where(VapiToolCall.created_at < text_before))
    recordings = await db.execute(
        update(Call)
        .where(Call.created_at < recording_before, Call.recording_url.is_not(None))
        .values(recording_url=None)
    )  # fmt: skip
    await db.commit()
    return {
        "transcripts": transcripts.rowcount or 0,  # type: ignore[attr-defined]
        "messages": messages.rowcount or 0,  # type: ignore[attr-defined]
        "recordings": recordings.rowcount or 0,  # type: ignore[attr-defined]
        "tool_calls": tool_calls.rowcount or 0,  # type: ignore[attr-defined]
    }
