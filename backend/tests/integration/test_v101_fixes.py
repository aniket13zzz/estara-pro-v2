"""v1.0.1 fixes: document search works on live channels; one waiting follow-up per lead."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.ratelimit import MemoryRateLimiter
from app.core.config import ClientConfig
from app.core.enums import Channel, FollowUpStatus
from app.db.models import Customer, FollowUp, Lead
from app.handoff.follow_ups import schedule_follow_up
from app.leads.state import ensure_open_lead
from app.main import create_app
from app.providers.embeddings import EmbeddingsNotConfigured, lazy_embedder
from app.providers.embeddings.local import HashingEmbedder
from app.truth.staleness import FixedClock
from tests.conftest import NOW, make_test_settings
from tests.fakes import ScriptedLLM, call, reply
from tests.integration.test_voice import ask, turn, voice  # noqa: F401  (fixture)
from tests.integration.test_whatsapp import APP_SECRET, VERIFY, Meta, deliver, inbound

pytestmark = pytest.mark.usefixtures("inventory")


def _tool_result(llm: ScriptedLLM, name: str) -> dict[str, Any]:
    """The result the brain handed back to the model for tool ``name``."""
    for request in llm.requests:
        for t in request.turns:
            for r in t.tool_results:
                if r.name == name:
                    content: dict[str, Any] = r.content
                    return content
    raise AssertionError(f"{name} was never answered")


async def test_phone_turn_really_searches_documents(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM],  # noqa: F811
) -> None:
    app, client, llm = voice
    app.state.embedder = HashingEmbedder()
    llm.steps += [call("search_knowledge", question="Is there a swimming pool?"),
                  reply("Our team will confirm that for you.")]  # fmt: skip
    await ask(client, turn("Is there a swimming pool in the project?"))
    # Before the fix the phone path had no embedder and always answered "not configured".
    assert _tool_result(llm, "search_knowledge")["reason"] == "no_matching_document"


async def test_missing_search_key_is_a_safe_answer(
    voice: tuple[FastAPI, AsyncClient, ScriptedLLM],  # noqa: F811
    client_config: ClientConfig,
) -> None:
    app, client, llm = voice
    gemini = client_config.embeddings.model_copy(update={"provider": "gemini"})
    config = client_config.model_copy(update={"embeddings": gemini})
    app.state.client_config = config  # Gemini embeddings without a GEMINI_API_KEY
    with pytest.raises(EmbeddingsNotConfigured):
        lazy_embedder(object(), config, app.state.settings)()
    llm.steps += [call("search_knowledge", question="Is there a swimming pool?"),
                  reply("Our team will confirm that for you.")]  # fmt: skip
    await ask(client, turn("Is there a swimming pool in the project?"))
    result = _tool_result(llm, "search_knowledge")
    assert result["status"] == "unavailable"
    assert result["reason"] == "knowledge_search_not_configured"


@pytest.fixture
async def lead_and_customer(
    db: AsyncSession, client_config: ClientConfig
) -> AsyncIterator[tuple[Lead, Customer]]:
    customer = Customer(preferred_language="en")
    db.add(customer)
    await db.flush()
    lead = await ensure_open_lead(db, customer, Channel.WHATSAPP)
    yield lead, customer


async def test_asking_twice_keeps_one_follow_up_at_the_latest_time(
    db: AsyncSession, client_config: ClientConfig, lead_and_customer: tuple[Lead, Customer]
) -> None:
    lead, customer = lead_and_customer
    common: dict[str, Any] = {"lead": lead, "customer": customer, "language": "en", "now": NOW}
    first = await schedule_follow_up(db, client_config, days=3, **common)
    second = await schedule_follow_up(db, client_config, days=7, **common)
    assert first.status == "scheduled" and second.status == "rescheduled"
    rows = (await db.execute(select(FollowUp))).scalars().all()
    assert len(rows) == 1
    assert rows[0].scheduled_for == NOW + timedelta(days=7)
    # Once that one is sent, a new request schedules a new follow-up.
    rows[0].status = FollowUpStatus.SENT
    third = await schedule_follow_up(db, client_config, days=1, **common)
    assert third.status == "scheduled"
    assert len((await db.execute(select(FollowUp))).scalars().all()) == 2


async def test_whatsapp_message_really_searches_documents(
    migrated_db: str, client_config: ClientConfig, db: AsyncSession
) -> None:
    settings = make_test_settings(migrated_db, whatsapp_app_secret=APP_SECRET,
                                  whatsapp_verify_token=VERIFY)  # fmt: skip
    app = create_app(config=client_config, settings=settings, rate_limiter=MemoryRateLimiter(),
                     use_redis=False)  # fmt: skip
    llm, meta = ScriptedLLM([]), Meta()
    app.state.clock, app.state.llm, app.state.whatsapp_client = FixedClock(NOW), llm, meta.client()
    app.state.embedder = HashingEmbedder()
    llm.steps += [call("search_knowledge", question="Is there a swimming pool?"),
                  reply("Our team will confirm that for you.")]  # fmt: skip
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://api.test") as c:
            r = await deliver(c, inbound("Is there a swimming pool in the project?"))
    assert r.status_code == 200 and len(meta.sent) == 1
    assert _tool_result(llm, "search_knowledge")["reason"] == "no_matching_document"
