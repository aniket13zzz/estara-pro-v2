"""LLM adapter interface. Business logic imports this module, never a vendor SDK or URL.

One call shape for every provider: ``chat`` with a system prompt, a list of turns and tools.
``extract`` (structured JSON into a Pydantic model) and ``summarize`` are built on ``chat`` so
every provider supports them the same way.

Provider-native content is kept on assistant turns (``Turn.raw``) and sent back exactly as
received. Gemini requires this for function calling (``thoughtSignature``, see
``app/providers/llm/gemini.py``) and OpenAI's Responses API needs reasoning items echoed.

``InstrumentedLLM`` adds timeouts (inside each adapter), retries with exponential backoff on
transient failures, latency measurement and token/cost recording.
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import ClientConfig
from app.core.settings import Settings


class LLMError(Exception):
    """The provider failed in a way retrying will not fix (bad request, auth, blocked)."""

    transient = False


class LLMTransientError(LLMError):
    """Timeouts, rate limits and 5xx responses. Retried with backoff."""

    transient = True


class LLMNotConfigured(LLMError):
    """The configured provider cannot be used (for example, its API key is missing)."""


class LLMTimeout(LLMTransientError):
    """No complete response within the read timeout. The provider may still have processed
    (and counted) the request, so it gets a smaller retry budget than other errors."""


class LLMUnavailable(LLMTransientError):
    """HTTP 5xx: the provider is down or overloaded (Gemini answers 503 UNAVAILABLE "This model
    is currently experiencing high demand"). Not the caller's fault; usually temporary."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMRateLimited(LLMTransientError):
    """HTTP 429. ``retry_after`` is the provider's requested wait in seconds when it sent one;
    ``daily`` is True when the message says a per-day quota is used up, which waiting a few
    seconds will not fix."""

    def __init__(self, message: str, *, retry_after: float | None = None,
                 daily: bool = False) -> None:  # fmt: skip
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: dict[str, Any]


@dataclass
class Turn:
    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    raw: Any = None  # provider-native assistant content, echoed back exactly


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0  # includes thinking/reasoning tokens where the provider bills them

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    raw: Any
    usage: Usage
    finish_reason: str = ""
    latency_ms: int = 0
    model: str = ""


class LLMClient(Protocol):
    provider: str
    model: str

    async def chat(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[ToolSpec],
        temperature: float,
        max_output_tokens: int = 1024,
        json_output: bool = False,
    ) -> ChatResult: ...


RETRY_DELAY = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s*$")
DAILY_QUOTA = re.compile(r"quota_exceeded|per ?day|perday|daily", re.IGNORECASE)


def retry_after_seconds(response: httpx.Response, now: float | None = None) -> float | None:
    """The wait a 429 asks for, if it says.

    - ``Retry-After`` header, in seconds or as an HTTP date (RFC 9110; sent by the Claude and
      OpenAI APIs).
    - Google's standard error model: ``error.details[]`` with ``@type`` ending in
      ``google.rpc.RetryInfo`` and ``retryDelay`` such as ``"13s"``. The Gemini error docs
      (https://ai.google.dev/gemini-api/docs/api-errors, checked 2026-10-13) do not describe
      the 429 body, so this is read when present and never required.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                when = email.utils.parsedate_to_datetime(header)
            except (TypeError, ValueError):
                when = None
            if when is not None:
                return max(0.0, when.timestamp() - (now if now is not None else time.time()))
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    details = error.get("details") if isinstance(error, dict) else None
    for item in details if isinstance(details, list) else []:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            match = RETRY_DELAY.match(str(item.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def http_error(provider: str, response: httpx.Response) -> LLMError:
    status = response.status_code
    message = f"{provider} returned HTTP {status}"
    if status == 429:
        wait = retry_after_seconds(response)
        daily = bool(DAILY_QUOTA.search(response.text[:4000]))
        detail = "daily quota" if daily else "rate limit"
        hint = f", retry after {wait:g}s" if wait is not None else ""
        return LLMRateLimited(f"{message} ({detail}{hint})", retry_after=wait, daily=daily)
    if status in (500, 502, 503, 504, 529):
        detail = _error_message(response)
        wait = retry_after_seconds(response)
        return LLMUnavailable(f"{message}{f': {detail}' if detail else ''}", retry_after=wait)
    if status in (408, 409):
        return LLMTransientError(message)
    return LLMError(message)


def _error_message(response: httpx.Response) -> str:
    """The provider's own error message, shortened (no request content is echoed in it)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message)[:160] if message else ""


def transport_error(provider: str, exc: httpx.HTTPError) -> LLMError:
    message = f"{provider} request failed: {type(exc).__name__}"
    if isinstance(exc, httpx.TimeoutException):
        return LLMTimeout(message)
    return LLMTransientError(message)


CONNECT_TIMEOUT = 10.0


def http_timeout(read_seconds: float) -> httpx.Timeout:
    """``read_seconds`` is how long to wait for the response. These calls are not streamed,
    so the model's whole answer, thinking included, must arrive within it. Connecting,
    sending and getting a pooled connection are capped separately at 10 s."""
    short = min(CONNECT_TIMEOUT, read_seconds)
    return httpx.Timeout(read_seconds, connect=short, write=short, pool=short)


# --------------------------------------------------------------------------- instrumented

UsageRecorder = Callable[[ChatResult, str], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    """How ``InstrumentedLLM`` retries.

    - Transient errors (5xx "unavailable/overloaded", connection failures): ``max_attempts``
      with exponential backoff from ``base_delay``, or the provider's ``Retry-After`` when it
      sends one (up to ``max_rate_limit_wait``).
    - Read timeouts: ``timeout_attempts`` (default 2, one retry). A slow request has usually
      used provider quota already, and a third identical wait rarely helps.
    - Rate limits (429): ``rate_limit_attempts``; each wait is the provider's ``Retry-After``
      if it sent one, else backoff from ``rate_limit_base_delay``. A wait longer than
      ``max_rate_limit_wait``, or a used-up daily quota, fails at once instead of hanging.

    Defaults suit a live customer, who should not wait long: production gives up after about
    10 seconds and hands the conversation to a person. The eval runner uses a patient policy.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    rate_limit_attempts: int = 3
    rate_limit_base_delay: float = 2.0
    max_rate_limit_wait: float = 10.0
    timeout_attempts: int = 2

    @classmethod
    def from_settings(cls, settings: Settings) -> RetryPolicy:
        return cls(
            max_attempts=settings.llm_max_attempts,
            timeout_attempts=settings.llm_timeout_attempts,
            rate_limit_attempts=settings.llm_rate_limit_attempts,
            max_rate_limit_wait=settings.llm_max_rate_limit_wait_seconds,
        )


class Pacer:
    """Keeps at least ``min_interval`` seconds between the starts of LLM calls, shared by every
    turn that uses it, and holds further calls after a 429 that asked for a wait."""

    def __init__(
        self,
        min_interval: float = 0.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.min_interval = max(0.0, min_interval)
        self.clock = clock
        self.sleep = sleep
        self._next = 0.0

    async def wait(self) -> float:
        """Sleep until the next call may start; returns the seconds slept."""
        delay = self._next - self.clock()
        if delay > 0:
            await self.sleep(delay)
        self._next = max(self.clock(), self._next) + self.min_interval
        return max(0.0, delay)

    def hold(self, seconds: float) -> None:
        self._next = max(self._next, self.clock() + seconds)


class InstrumentedLLM:
    """Retries transient failures with backoff (honouring rate-limit waits), paces calls, and
    records usage for each successful call."""

    def __init__(
        self,
        inner: LLMClient,
        *,
        max_attempts: int | None = None,
        base_delay: float | None = None,
        policy: RetryPolicy | None = None,
        pacer: Pacer | None = None,
        recorder: UsageRecorder | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_wait: Callable[[str], None] | None = None,
    ) -> None:
        policy = policy or RetryPolicy()
        overrides: dict[str, Any] = {}
        if max_attempts is not None:
            overrides["max_attempts"] = max_attempts
        if base_delay is not None:
            overrides["base_delay"] = base_delay
        if overrides:
            policy = RetryPolicy(**{**policy.__dict__, **overrides})
        self.inner = inner
        self.provider = inner.provider
        self.model = inner.model
        self.policy = policy
        self.max_attempts = max(1, policy.max_attempts)
        self.pacer = pacer
        self.recorder = recorder
        self.sleep = sleep
        self.on_wait = on_wait
        # Request counters (every attempt is a request the provider may count against quota).
        self.attempts_used = 0
        self.succeeded = 0
        self.rate_limited = 0  # 429 responses
        self.timeouts = 0
        self.unavailable = 0  # 5xx responses

    def stats(self) -> dict[str, int]:
        return {"requests": self.attempts_used, "succeeded": self.succeeded,
                "rate_limited": self.rate_limited, "timeouts": self.timeouts,
                "unavailable": self.unavailable}  # fmt: skip

    async def chat(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[ToolSpec],
        temperature: float,
        max_output_tokens: int = 1024,
        json_output: bool = False,
        purpose: str = "chat",
    ) -> ChatResult:
        policy = self.policy
        errors = limits = slow = 0
        while True:
            if self.pacer is not None:
                await self.pacer.wait()
            self.attempts_used += 1
            started = time.perf_counter()
            try:
                result = await self.inner.chat(
                    system=system,
                    turns=turns,
                    tools=tools,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    json_output=json_output,
                )
            except LLMRateLimited as exc:
                self.rate_limited += 1
                limits += 1
                if exc.daily or limits >= max(1, policy.rate_limit_attempts):
                    raise
                backoff = policy.rate_limit_base_delay * (2 ** (limits - 1))
                delay = exc.retry_after if exc.retry_after is not None else backoff
                if delay > policy.max_rate_limit_wait:
                    raise
                await self._pause(delay + random.uniform(0, max(delay, 0.1) / 10), str(exc))  # noqa: S311
                continue
            except LLMTimeout as exc:
                self.timeouts += 1
                slow += 1
                if slow >= max(1, policy.timeout_attempts):
                    raise
                if self.on_wait is not None:
                    self.on_wait(f"{exc}; retrying once")
                await self.sleep(policy.base_delay)
                continue
            except LLMError as exc:
                errors += 1
                if isinstance(exc, LLMUnavailable):
                    self.unavailable += 1
                if not exc.transient or errors >= self.max_attempts:
                    raise
                delay = policy.base_delay * (2 ** (errors - 1))
                hint = getattr(exc, "retry_after", None)
                if hint is not None and hint <= policy.max_rate_limit_wait:
                    delay = max(delay, hint)
                jittered = delay + random.uniform(0, delay / 4)  # noqa: S311
                if isinstance(exc, LLMUnavailable) and delay >= 1:
                    await self._pause(jittered, str(exc))  # long enough to be worth reporting
                else:
                    await self.sleep(jittered)
                continue
            self.succeeded += 1
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            result.model = result.model or self.model
            if self.recorder is not None:
                await self.recorder(result, purpose)
            return result

    async def _pause(self, seconds: float, reason: str) -> None:
        if self.on_wait is not None:
            self.on_wait(f"{reason}; waiting {seconds:.0f}s")
        if self.pacer is not None:
            self.pacer.hold(seconds)  # later calls wait too
        await self.sleep(seconds)


# ------------------------------------------------------------------- extract / summarize

JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    match = JSON_BLOCK.search(cleaned)
    if not match:
        raise ValueError("no JSON object in the response")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("the JSON response is not an object")
    return value


async def extract[M: BaseModel](
    llm: InstrumentedLLM, *, instructions: str, text: str, schema: type[M], attempts: int = 2
) -> M:
    """Structured extraction: the model returns JSON validated against ``schema``."""
    system = (
        f"{instructions}\n\nReturn only one JSON object matching this JSON Schema, with no "
        f"other text:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
    turns = [Turn("user", text)]
    error = ""
    for _ in range(attempts):
        result = await llm.chat(
            system=system, turns=turns, tools=[], temperature=0.0, json_output=True,
            purpose="extract",
        )  # fmt: skip
        try:
            return schema.model_validate(parse_json_object(result.text))
        except (ValueError, ValidationError) as exc:
            error = str(exc)[:300]
            retry = Turn("user", f"That was not valid: {error}. Return only the JSON object.")
            turns = [*turns, Turn("assistant", result.text, raw=result.raw), retry]
    raise LLMError(f"extraction failed: {error}")


async def summarize(llm: InstrumentedLLM, *, text: str, instructions: str) -> str:
    result = await llm.chat(
        system=instructions, turns=[Turn("user", text)], tools=[], temperature=0.2,
        purpose="summarize",
    )  # fmt: skip
    return result.text.strip()


# --------------------------------------------------------------------------------- factory


def cost_usd(config: ClientConfig, usage: Usage) -> float | None:
    pricing = config.llm.pricing
    if pricing is None:
        return None
    return (
        usage.input_tokens * pricing.input_usd_per_mtok
        + usage.output_tokens * pricing.output_usd_per_mtok
    ) / 1_000_000


def get_llm(
    config: ClientConfig, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> LLMClient:
    """The provider named in ``llm.provider``, with its key from ``.env``."""
    provider = config.llm.provider
    timeout = settings.llm_timeout_seconds
    if provider == "gemini":
        from app.providers.llm.gemini import GeminiLLM

        if settings.gemini_api_key is None:
            raise LLMNotConfigured("GEMINI_API_KEY is not set in .env")
        return GeminiLLM(settings.gemini_api_key.get_secret_value(), config.llm.model,
                         transport=transport, timeout=timeout,
                         thinking_level=config.llm.thinking_level)  # fmt: skip
    if provider == "claude":
        from app.providers.llm.claude import ClaudeLLM

        if settings.anthropic_api_key is None:
            raise LLMNotConfigured("ANTHROPIC_API_KEY is not set in .env")
        return ClaudeLLM(settings.anthropic_api_key.get_secret_value(), config.llm.model,
                         transport=transport, timeout=timeout)  # fmt: skip
    if provider == "openai":
        from app.providers.llm.openai import OpenAILLM

        if settings.openai_api_key is None:
            raise LLMNotConfigured("OPENAI_API_KEY is not set in .env")
        return OpenAILLM(settings.openai_api_key.get_secret_value(), config.llm.model,
                         transport=transport, timeout=timeout)  # fmt: skip
    raise LLMNotConfigured(f"unknown llm provider {provider!r}")
