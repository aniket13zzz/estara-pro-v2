"""HTTP 429 handling: wait hints, retry policy, pacing, and how the eval runner reports limits."""

from __future__ import annotations

import email.utils
from typing import Any

import httpx
import pytest

from app.providers.llm import (
    InstrumentedLLM,
    LLMRateLimited,
    LLMTransientError,
    Pacer,
    RetryPolicy,
    Turn,
    http_error,
    retry_after_seconds,
)
from app.providers.llm.gemini import GeminiLLM
from tests.evals.checks import CheckResult
from tests.evals.run import (
    FAILED,
    NOT_RUN,
    PASSED,
    RATE_LIMITED,
    SMOKE_IDS,
    Conversation,
    Outcome,
    exit_code,
    load_conversations,
    main,
    run_all,
    summary,
)
from tests.fakes import ScriptedLLM, reply

GEMINI_429 = {
    "error": {
        "code": 429,
        "message": "Resource has been exhausted. Quota exceeded for metric: requests per minute.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "13s"}],
    }
}  # shape of Google's standard error model; not shown on the Gemini error docs page


def response(status: int = 429, *, headers: dict[str, str] | None = None,
             json: Any = None, text: str | None = None) -> httpx.Response:  # fmt: skip
    if text is not None:
        return httpx.Response(status, headers=headers, text=text)
    return httpx.Response(status, headers=headers, json=json if json is not None else {})


# ------------------------------------------------------------------------- wait hints


def test_retry_after_header_in_seconds_and_as_a_date() -> None:
    assert retry_after_seconds(response(headers={"Retry-After": "7"})) == 7.0
    when = email.utils.formatdate(1_000_030, usegmt=True)
    assert retry_after_seconds(response(headers={"Retry-After": when}), now=1_000_000) == 30.0


def test_google_retry_info_in_the_body() -> None:
    assert retry_after_seconds(response(json=GEMINI_429)) == 13.0
    assert retry_after_seconds(response(json={"error": {"code": 429}})) is None
    assert retry_after_seconds(response(text="not json")) is None


def test_429_becomes_rate_limited_with_daily_quota_detected() -> None:
    minute = http_error("gemini", response(json=GEMINI_429))
    assert isinstance(minute, LLMRateLimited) and minute.transient
    assert minute.retry_after == 13.0 and not minute.daily and "retry after 13s" in str(minute)
    daily_body = {
        "error": {"code": "quota_exceeded", "message": "You have exceeded your daily quota."}
    }
    daily = http_error("gemini", response(json=daily_body))
    assert isinstance(daily, LLMRateLimited) and daily.daily
    per_day = http_error("gemini", response(json={"error": {"message": "requests per day"}}))
    assert isinstance(per_day, LLMRateLimited) and per_day.daily
    assert not isinstance(http_error("gemini", response(503)), LLMRateLimited)


async def test_gemini_adapter_raises_rate_limited() -> None:
    llm = GeminiLLM("k", "m", transport=httpx.MockTransport(lambda r: response(json=GEMINI_429)))
    with pytest.raises(LLMRateLimited) as info:
        await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    assert info.value.retry_after == 13.0


# ---------------------------------------------------------------------- retry policy


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


async def _chat(llm: InstrumentedLLM) -> str:
    result = await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    return result.text


async def test_rate_limit_waits_what_the_provider_asks() -> None:
    sleeps = Sleeps()
    inner = ScriptedLLM([LLMRateLimited("429", retry_after=7), reply("ok")])
    llm = InstrumentedLLM(inner, policy=RetryPolicy(max_rate_limit_wait=60), sleep=sleeps)
    assert await _chat(llm) == "ok"
    assert len(sleeps.calls) == 1 and 7 <= sleeps.calls[0] <= 7.71
    assert llm.rate_limited == 1


async def test_rate_limit_without_a_hint_backs_off_exponentially() -> None:
    sleeps = Sleeps()
    inner = ScriptedLLM([LLMRateLimited("429")] * 3 + [reply("ok")])
    policy = RetryPolicy(rate_limit_attempts=4, rate_limit_base_delay=2, max_rate_limit_wait=60)
    assert await _chat(InstrumentedLLM(inner, policy=policy, sleep=sleeps)) == "ok"
    assert len(sleeps.calls) == 3
    for slept, base in zip(sleeps.calls, (2, 4, 8), strict=True):
        assert base <= slept <= base * 1.1  # backoff plus at most 10% jitter


async def test_rate_limit_gives_up_on_long_waits_daily_quota_and_attempts() -> None:
    sleeps = Sleeps()
    patient = RetryPolicy(max_rate_limit_wait=60)
    inner = ScriptedLLM([LLMRateLimited("429", retry_after=300)])
    long_wait = InstrumentedLLM(inner, policy=patient, sleep=sleeps)
    with pytest.raises(LLMRateLimited):
        await _chat(long_wait)
    daily = InstrumentedLLM(ScriptedLLM([LLMRateLimited("429", daily=True)]), sleep=sleeps)
    with pytest.raises(LLMRateLimited):
        await _chat(daily)
    assert sleeps.calls == [] and daily.attempts_used == 1
    capped = InstrumentedLLM(ScriptedLLM([LLMRateLimited("429", retry_after=0)] * 2),
                             policy=RetryPolicy(rate_limit_attempts=2), sleep=sleeps)  # fmt: skip
    with pytest.raises(LLMRateLimited):
        await _chat(capped)
    assert capped.attempts_used == 2


async def test_rate_limits_and_other_errors_have_separate_budgets() -> None:
    inner = ScriptedLLM([LLMTransientError("503"), LLMRateLimited("429", retry_after=0),
                         LLMTransientError("503"), reply("ok")])  # fmt: skip
    llm = InstrumentedLLM(inner, policy=RetryPolicy(max_attempts=3, base_delay=0), sleep=Sleeps())
    assert await _chat(llm) == "ok" and llm.attempts_used == 4


async def test_wait_notes_reach_the_caller() -> None:
    notes: list[str] = []
    inner = ScriptedLLM([LLMRateLimited("gemini returned HTTP 429", retry_after=5), reply("ok")])
    llm = InstrumentedLLM(inner, sleep=Sleeps(), on_wait=notes.append)
    await _chat(llm)
    assert notes and "waiting 5s" in notes[0]


# ----------------------------------------------------------------------------- pacing


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def test_pacer_spaces_calls_and_holds_after_a_429() -> None:
    clock = FakeClock()
    pacer = Pacer(10, clock=clock, sleep=clock.sleep)
    assert await pacer.wait() == 0  # first call goes at once
    clock.now += 3
    assert await pacer.wait() == pytest.approx(7)  # 10 s after the first call
    pacer.hold(30)
    assert await pacer.wait() == pytest.approx(30)


async def test_pacer_is_shared_by_instrumented_calls() -> None:
    clock = FakeClock()
    pacer = Pacer(6, clock=clock, sleep=clock.sleep)
    inner = ScriptedLLM([reply("a"), reply("b"), reply("c")])
    llm = InstrumentedLLM(inner, pacer=pacer, sleep=clock.sleep)
    for _ in range(3):
        await _chat(llm)
    assert clock.now == pytest.approx(112)  # two waits of 6 s


# --------------------------------------------------------------------- eval runner


def _conv(cid: str) -> Conversation:
    from tests.evals.checks import Expectation

    return Conversation(cid, "en", "en", "test", [("hello there friend", "en")], Expectation())


class FakeRunner:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = statuses
        self.lines: list[str] = []

    def say(self, line: str) -> None:
        self.lines.append(line)

    async def run_one(self, db: Any, conv: Conversation) -> Outcome:
        status = self.statuses.pop(0)
        note = ""
        if status == "daily":
            status, note = RATE_LIMITED, "turn 1: provider rate limit (HTTP 429); daily quota"
        elif status == RATE_LIMITED:
            note = "turn 1: provider rate limit (HTTP 429) after retries"
        elif status == "timed_out":
            note = "turn 1: no answer within the LLM timeout, after a retry"
        elif status == "provider_unavailable":
            note = "turn 1: provider unavailable (HTTP 5xx) after retries"
        checks = [CheckResult("language", status == PASSED)] if status in {PASSED, FAILED} else []
        # (provider problems carry no checks: they are not scored)
        return Outcome(conv.id, conv.style, conv.description, status, checks, {}, [], 1.0, note)


async def test_rate_limited_conversations_are_not_scored_as_failures() -> None:
    runner = FakeRunner([PASSED, RATE_LIMITED, PASSED])
    outcomes = await run_all(runner, None, [_conv("a"), _conv("b"), _conv("c")])  # type: ignore[arg-type]
    assert [o.status for o in outcomes] == [PASSED, RATE_LIMITED, PASSED]
    assert exit_code(outcomes) == 3  # incomplete, but nothing failed
    text = summary(outcomes, "gemini-test", offline=False)
    assert "passed 2, failed 0" in text and "Rate limited, not run: 1" in text
    assert "| b | en | rate limited, not run |" in text
    assert "| language | 2 | 2 |" in text  # the rate-limited one is not counted


async def test_daily_quota_or_three_limits_in_a_row_stop_the_run() -> None:
    daily = await run_all(FakeRunner(["daily", PASSED]), None,  # type: ignore[arg-type]
                          [_conv("a"), _conv("b")])  # fmt: skip
    assert [o.status for o in daily] == [RATE_LIMITED, NOT_RUN] and "daily quota" in daily[1].note
    streak = await run_all(FakeRunner([RATE_LIMITED] * 3), None,  # type: ignore[arg-type]
                           [_conv(c) for c in "abcd"])  # fmt: skip
    assert [o.status for o in streak] == [RATE_LIMITED] * 3 + [NOT_RUN]
    assert exit_code([*streak, Outcome("x", "en", "", FAILED)]) == 1


def test_selection_by_id_and_style(capsys: pytest.CaptureFixture[str]) -> None:
    hindi = load_conversations(styles=["hi"])
    assert len(hindi) == 11 and {c.style for c in hindi} == {"hi"}
    one = load_conversations(ids=["en_01_new_town_visit"])
    assert [c.id for c in one] == ["en_01_new_town_visit"]
    assert len(load_conversations(styles=["en", "hinglish"])) == 22
    smoke = load_conversations(ids=list(SMOKE_IDS))
    assert sorted(c.style for c in smoke) == ["en", "hi", "hinglish"]
    assert main(["--list"]) == 0 and "hg_10_off_topic_and_loan" in capsys.readouterr().out
    assert main(["--style", "tamil"]) == 2
    assert main(["--id", "no_such_conversation"]) == 2


# --------------------------------------------------------------------------- timeouts


def test_timeouts_become_llm_timeout_with_separate_connect_limit() -> None:
    from app.providers.llm import LLMTimeout, http_timeout, transport_error

    assert isinstance(transport_error("gemini", httpx.ReadTimeout("slow")), LLMTimeout)
    assert not isinstance(transport_error("gemini", httpx.ConnectError("down")), LLMTimeout)
    t = http_timeout(120)
    assert (t.read, t.connect, t.write, t.pool) == (120, 10, 10, 10)
    assert http_timeout(5).connect == 5


async def test_gemini_read_timeout_is_reported_as_timeout() -> None:
    from app.providers.llm import LLMTimeout

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no response", request=request)

    llm = GeminiLLM("k", "m", transport=httpx.MockTransport(slow), timeout=120)
    with pytest.raises(LLMTimeout, match="ReadTimeout"):
        await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)


async def test_a_timed_out_request_is_retried_once_and_counted() -> None:
    from app.providers.llm import LLMTimeout

    once = InstrumentedLLM(ScriptedLLM([LLMTimeout("ReadTimeout"), reply("ok")]), sleep=Sleeps())
    assert await _chat(once) == "ok"
    assert once.stats() == {"requests": 2, "succeeded": 1, "rate_limited": 0, "timeouts": 1,
                            "unavailable": 0}  # fmt: skip
    twice = InstrumentedLLM(ScriptedLLM([LLMTimeout("ReadTimeout")] * 3), sleep=Sleeps())
    with pytest.raises(LLMTimeout):
        await _chat(twice)
    assert twice.stats()["requests"] == 2  # not three: a slow request has used quota already


async def test_timed_out_conversations_are_reported_apart() -> None:
    from tests.evals.run import TIMED_OUT

    runner = FakeRunner([TIMED_OUT, TIMED_OUT, PASSED, TIMED_OUT])
    outcomes = await run_all(runner, None, [_conv(c) for c in "abcd"])  # type: ignore[arg-type]
    assert [o.status for o in outcomes] == [TIMED_OUT, TIMED_OUT, PASSED, TIMED_OUT]
    assert exit_code(outcomes) == 3
    outcomes[2].llm = {"requests": 5, "succeeded": 5, "rate_limited": 0, "timeouts": 0,
                       "unavailable": 0}  # fmt: skip
    outcomes[2].turns_run = 2
    text = summary(outcomes, "gemini-test", offline=False)
    assert "Timed out, not run: 3" in text and "LLM requests sent: 5" in text
    assert "2.5 requests per customer turn over 2 turns" in text


async def test_three_provider_problems_in_a_row_stop_the_run() -> None:
    from tests.evals.run import TIMED_OUT, UNAVAILABLE

    statuses = [UNAVAILABLE, TIMED_OUT, RATE_LIMITED, PASSED]
    outcomes = await run_all(FakeRunner(statuses), None,  # type: ignore[arg-type]
                             [_conv(c) for c in "abcd"])  # fmt: skip
    assert [o.status for o in outcomes] == [UNAVAILABLE, TIMED_OUT, RATE_LIMITED, NOT_RUN]
    assert "could not run" in outcomes[3].note
    text = summary(outcomes, "gemini-test", offline=False)
    assert "Provider unavailable, not run: 1" in text
    assert "| a | en | provider unavailable, not run |" in text


# --------------------------------------------------------------------- provider 5xx


def test_503_high_demand_is_unavailable_with_the_providers_message() -> None:
    from app.providers.llm import LLMUnavailable

    body = {"error": {"code": 503, "status": "UNAVAILABLE", "message": "This model is "
                      "currently experiencing high demand. Spikes in demand are usually "
                      "temporary. Please try again later."}}  # fmt: skip
    error = http_error("Gemini", response(503, json=body, headers={"Retry-After": "20"}))
    assert isinstance(error, LLMUnavailable) and error.transient
    assert "high demand" in str(error) and error.retry_after == 20.0
    assert isinstance(http_error("Gemini", response(500, text="oops")), LLMUnavailable)
    assert not isinstance(http_error("Gemini", response(408)), LLMUnavailable)


async def test_unavailable_backs_off_reports_waits_and_counts() -> None:
    from app.providers.llm import LLMUnavailable

    sleeps, notes = Sleeps(), []
    inner = ScriptedLLM([LLMUnavailable("Gemini returned HTTP 503: high demand")] * 2
                        + [reply("ok")])  # fmt: skip
    llm = InstrumentedLLM(inner, policy=RetryPolicy(max_attempts=4, base_delay=10),
                          sleep=sleeps, on_wait=notes.append)  # fmt: skip
    assert await _chat(llm) == "ok"
    assert 10 <= sleeps.calls[0] <= 12.5 and 20 <= sleeps.calls[1] <= 25
    assert len(notes) == 2 and "high demand" in notes[0]
    assert llm.stats()["unavailable"] == 2 and llm.stats()["requests"] == 3
    hinted = InstrumentedLLM(ScriptedLLM([LLMUnavailable("503", retry_after=7), reply("ok")]),
                             policy=RetryPolicy(base_delay=0.5), sleep=sleeps)  # fmt: skip
    await _chat(hinted)
    assert 7 <= sleeps.calls[-1] <= 8.75  # the provider's Retry-After wins over 0.5 s


def test_report_file_names_keep_the_model_version(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.evals.run as run_module

    monkeypatch.setattr(run_module, "RESULTS", tmp_path)
    outcome = Outcome("en_01", "en", "", RATE_LIMITED, note="turn 1: provider daily quota")
    json_path, md_path = run_module.write_report([outcome], "gemini-gemini-3.8-flash", False, "")
    assert json_path.name.endswith("-gemini-gemini-3.8-flash.json")
    assert md_path.name.endswith("-gemini-gemini-3.8-flash.md")
    assert json_path.exists() and md_path.exists()
