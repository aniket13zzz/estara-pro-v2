"""Step B: the pause button, the home summary, monthly usage limits and data retention."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ClientConfig
from app.db.models import AuditLog, Call, Message
from app.jobs.retention import REMOVED, apply_retention
from tests.fakes import ScriptedLLM
from tests.helpers import ADMIN, STAFF, login
from tests.integration.test_voice import AUTH, _demo_call, fixture, make_app, ready, voice_settings

pytestmark = pytest.mark.usefixtures("inventory")


@pytest.fixture
async def stack(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> AsyncIterator[tuple[FastAPI, AsyncClient, ScriptedLLM]]:
    llm = ScriptedLLM([])
    settings = voice_settings(migrated_db, vapi_assistant_id="asst-1")
    app = make_app(ready(client_config), settings, llm)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            yield app, c, llm


async def assistant_request(client: AsyncClient) -> dict[str, object]:
    r = await client.post("/api/v1/webhooks/vapi/events", json=fixture("assistant_request.json"),
                          headers=AUTH)  # fmt: skip
    data: dict[str, object] = r.json()
    return data


async def test_only_admins_pause_and_every_call_then_goes_to_a_person(
    stack: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession
) -> None:
    _, client, _ = stack
    assert await assistant_request(client) == {"assistantId": "asst-1"}
    staff = await login(client, STAFF)
    r = await client.post("/api/v1/controls/pause", json={"paused": True}, headers=staff)
    assert r.status_code == 403
    admin = await login(client, ADMIN)
    holiday = {"paused": True, "reason": "Diwali holiday"}
    r = await client.post("/api/v1/controls/pause", json=holiday, headers=admin)
    assert r.json() == {"paused": True, "paused_reason": "Diwali holiday"}
    assert "destination" in await assistant_request(client)
    assert (await client.get("/api/v1/summary")).json()["paused"] is True
    await client.post("/api/v1/controls/pause", json={"paused": False}, headers=admin)
    assert await assistant_request(client) == {"assistantId": "asst-1"}
    query = select(AuditLog.action).where(AuditLog.action.like("agent.%"))
    actions = (await db.execute(query.order_by(AuditLog.created_at))).scalars().all()
    assert actions == ["agent.pause", "agent.resume"]


async def test_home_summary_after_the_demo_call(
    stack: tuple[FastAPI, AsyncClient, ScriptedLLM],
) -> None:
    _, client, llm = stack
    await _demo_call(client, llm)
    assert (await client.get("/api/v1/summary")).status_code == 401
    await login(client, STAFF)
    data = (await client.get("/api/v1/summary")).json()
    assert data["today"]["new_leads"] == 1 and data["today"]["calls"] == 1
    assert data["today"]["visits_requested"] == 1 and data["waiting_confirmation"] == 1
    assert data["last_30_days"] == {"qualified_leads": 1, "visits_booked": 1,
                                    "visit_booking_rate": 1.0}  # fmt: skip
    usage = data["usage"]
    assert usage["calls"] == 1 and usage["call_minutes"] == pytest.approx(3.3)
    assert usage["ai_requests"] >= 3 and usage["warnings"] == []


async def test_call_minute_limit_sends_calls_to_a_person(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> None:
    limits = client_config.usage_limits.model_copy(update={"monthly_call_minutes": 3})
    config = ready(client_config).model_copy(update={"usage_limits": limits})
    llm = ScriptedLLM([])
    app = make_app(config, voice_settings(migrated_db, vapi_assistant_id="asst-1"), llm)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            assert await assistant_request(c) == {"assistantId": "asst-1"}
            await _demo_call(c, llm)  # a 3 min 20 s call
            assert "destination" in await assistant_request(c)
            await login(c, STAFF)
            warnings = (await c.get("/api/v1/summary")).json()["usage"]["warnings"]
    assert warnings and warnings[0].startswith("Call minutes")


async def test_retention_removes_old_text_and_recording_links(
    stack: tuple[FastAPI, AsyncClient, ScriptedLLM], db: AsyncSession, client_config: ClientConfig
) -> None:
    _, client, llm = stack
    await _demo_call(client, llm)
    created = (await db.execute(select(Call))).scalar_one().created_at
    # Nothing is old yet (rows are stamped with the database's own clock).
    assert await apply_retention(db, client_config, created) == {
        "transcripts": 0, "messages": 0, "recordings": 0, "tool_calls": 0}  # fmt: skip
    later = created + timedelta(days=client_config.retention.recording_days + 1)
    counts = await apply_retention(db, client_config, later)
    assert counts["recordings"] == 1 and counts["transcripts"] == 0
    much_later = created + timedelta(days=client_config.retention.transcript_days + 1)
    counts = await apply_retention(db, client_config, much_later)
    assert counts["transcripts"] == 1 and counts["messages"] > 0
    call = (await db.execute(select(Call))).scalar_one()
    await db.refresh(call)
    assert call.transcript == REMOVED and call.recording_url is None and call.summary_en is None
    left = await db.scalar(select(func.count(Message.id)).where(Message.content != REMOVED))
    assert left == 0
    assert await apply_retention(db, client_config, much_later) == {
        "transcripts": 0, "messages": 0, "recordings": 0, "tool_calls": 0}  # fmt: skip
