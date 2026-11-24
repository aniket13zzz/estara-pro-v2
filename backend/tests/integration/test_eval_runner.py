"""The eval runner against the real Sales Brain and database: a provider 429 that survives the
retries is reported as rate limited (not run), never as a scored Sales Brain failure."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.scripts import Scripts
from app.core.config import ClientConfig
from app.providers.llm import LLMRateLimited, LLMTimeout, Pacer, RetryPolicy
from app.providers.llm.offline import OfflineLLM
from tests.evals.run import (
    NOT_RUN,
    RATE_LIMITED,
    TIMED_OUT,
    Runner,
    load_conversations,
    run_all,
)
from tests.fakes import ScriptedLLM

pytestmark = pytest.mark.usefixtures("inventory")
NO_WAIT = RetryPolicy(base_delay=0, rate_limit_attempts=2, rate_limit_base_delay=0)


async def test_rate_limited_conversation_is_not_run_and_streak_stops(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    llm = ScriptedLLM([LLMRateLimited("gemini returned HTTP 429", retry_after=0)] * 6)
    lines: list[str] = []
    runner = Runner(client_config, llm, Scripts(client_config), NO_WAIT, Pacer(0),
                    say=lines.append)  # fmt: skip
    conversations = load_conversations(styles=["en"])[:4]
    outcomes = await run_all(runner, db, conversations)
    assert [o.status for o in outcomes] == [RATE_LIMITED] * 3 + [NOT_RUN]
    assert outcomes[0].checks == [] and "HTTP 429" in outcomes[0].note
    assert len(llm.requests) == 6  # two attempts per conversation, then the run stopped
    assert any("in a row could not run" in line for line in lines)


async def test_daily_quota_stops_the_run(db: AsyncSession, client_config: ClientConfig) -> None:
    llm = ScriptedLLM([LLMRateLimited("429: You have exceeded your daily quota", daily=True)])
    runner = Runner(client_config, llm, Scripts(client_config), NO_WAIT, Pacer(0),
                    say=lambda _: None)  # fmt: skip
    outcomes = await run_all(runner, db, load_conversations(styles=["hi"])[:3])
    assert [o.status for o in outcomes] == [RATE_LIMITED, NOT_RUN, NOT_RUN]
    assert "daily quota" in outcomes[1].note


async def test_conversations_that_run_are_scored(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    runner = Runner(client_config, OfflineLLM(), Scripts(client_config), NO_WAIT, Pacer(0),
                    say=lambda _: None)  # fmt: skip
    [outcome] = await run_all(runner, db, load_conversations(ids=["en_01_new_town_visit"]))
    assert outcome.status in {"passed", "failed"} and outcome.checks
    assert {c.name for c in outcome.checks} >= {"numbers_grounded", "language", "visit_offered"}


async def test_slow_provider_is_timed_out_not_a_brain_failure(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    llm = ScriptedLLM([LLMTimeout("Gemini request failed: ReadTimeout")] * 2)
    runner = Runner(client_config, llm, Scripts(client_config), NO_WAIT, Pacer(0),
                    say=lambda _: None)  # fmt: skip
    [outcome] = await run_all(runner, db, load_conversations(ids=["hg_01_new_town_visit"]))
    assert outcome.status == TIMED_OUT and outcome.checks == []
    assert "ReadTimeout" in outcome.note and outcome.turns_run == 0
    assert outcome.llm == {"requests": 2, "succeeded": 0, "rate_limited": 0, "timeouts": 2,
                           "unavailable": 0}  # fmt: skip


async def test_provider_outage_is_unavailable_not_a_brain_failure(
    db: AsyncSession, client_config: ClientConfig
) -> None:
    from app.providers.llm import LLMUnavailable
    from tests.evals.run import UNAVAILABLE

    llm = ScriptedLLM([LLMUnavailable("Gemini returned HTTP 503: high demand")] * 3)
    policy = RetryPolicy(max_attempts=3, base_delay=0)
    runner = Runner(client_config, llm, Scripts(client_config), policy, Pacer(0),
                    say=lambda _: None)  # fmt: skip
    [outcome] = await run_all(runner, db, load_conversations(ids=["hi_01_new_town_visit"]))
    assert outcome.status == UNAVAILABLE and outcome.checks == []
    assert "high demand" in outcome.note and outcome.llm["unavailable"] == 3
