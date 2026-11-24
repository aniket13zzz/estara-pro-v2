"""Gemini latency probe: is a slow reply the network, or the request?

    python -m app.brain.probe                    # the configured model and settings
    python -m app.brain.probe --thinking medium  # compare a thinking level
    python -m app.brain.probe --temperature 0.3  # compare a temperature
    python -m app.brain.probe --model gemini-3.6-flash   # another model, same requests

Four timed calls, printed as they finish:

0. ``network``: ``GET models/{model}``, no generation. Network and TLS only.
1. ``tiny``: "ping" with a one-line system prompt and no tools. Network plus the smallest
   generation.
2. ``brain``: the Sales Brain's real first request: the English system prompt, all 11 tool
   definitions and a customer message, with the configured temperature, thinking level and
   output limit.
3. ``brain+tool``: the follow-up after step 2's tool calls, sending back fixed, made-up tool
   results (the probe touches no database). This is the request shape that timed out in
   en_01.

Each line shows seconds, HTTP status, Gemini's token counts (prompt, answer, thinking) and the
finish reason. If 0 and 1 are slow, look at the network; if they are fast and 2 or 3 are
slow, the request is the problem. Steps 1–3 are three generation requests against the
project's quota.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any

import httpx

from app.brain.prompt import TurnNotes, build_system_prompt
from app.brain.scripts import Scripts
from app.brain.service import REPLY_MAX_TOKENS
from app.brain.tools import tool_specs
from app.core.config import ConfigError, load_config
from app.core.enums import Channel
from app.core.settings import Settings
from app.providers.llm import ToolResult, Turn, http_timeout
from app.providers.llm.gemini import BASE_URL, GeminiLLM

CUSTOMER = "I'm looking for a 2 BHK in New Town, budget around 80 lakh"
FAKE_RESULTS: dict[str, dict[str, Any]] = {
    "save_requirements": {"saved": ["bhk", "locations", "budget_max_inr"], "qualified": True,
                          "missing": [], "lead_state": "qualified"},
    "search_properties": {"matches": [{"project_code": "SG", "project": "Sunrise Greens "
                          "(Sample)", "bhk": 2, "price": "72 to 78 lakh rupees",
                          "reason": "within your budget, in your preferred area"}]},
    "get_visit_slots": {"project_code": "SG", "slots": [{"slot_id": "2026-11-07T11:00",
                        "slot": "Saturday 7 November at 11 am"}]},
}  # fmt: skip
OTHER_RESULT = {
    "status": "unavailable",
    "instruction": "Probe: no data. Say the team will confirm.",
}


def _usage(data: dict[str, Any]) -> str:
    u = data.get("usageMetadata") or {}
    return (
        f"prompt {u.get('promptTokenCount', '?')} / answer "
        f"{u.get('candidatesTokenCount', 0)} / thinking {u.get('thoughtsTokenCount', 0)}"
    )


def _what(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
        return f"no answer ({reason})"
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    calls = [p["functionCall"]["name"] for p in parts if "functionCall" in p]
    text = " ".join(p.get("text", "") for p in parts if not p.get("thought"))
    shown = f"tools {calls}" if calls else repr(text.strip()[:60])
    return f"{candidate.get('finishReason', '?')}, {shown}"


async def main_async(
    args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    def say(line: str) -> None:
        print(line, flush=True)

    try:
        config = load_config()
    except ConfigError as exc:
        say(str(exc))
        return 1
    settings = Settings()
    if config.llm.provider != "gemini":
        say(f"The probe supports Gemini only; llm.provider is {config.llm.provider!r}.")
        return 2
    if settings.gemini_api_key is None:
        say("GEMINI_API_KEY is not set in .env.")
        return 2
    thinking = config.llm.thinking_level if args.thinking is None else (
        None if args.thinking == "default" else args.thinking)  # fmt: skip
    temperature = config.llm.temperature if args.temperature is None else args.temperature
    key = settings.gemini_api_key.get_secret_value()
    model = args.model or config.llm.model
    llm = GeminiLLM(key, model, timeout=args.timeout, thinking_level=thinking)
    say(f"Gemini probe: model {llm.model}, temperature {temperature:g}, thinking "
        f"{thinking or 'model default'}, timeout {args.timeout:g}s")  # fmt: skip
    failures = 0

    async with httpx.AsyncClient(timeout=http_timeout(args.timeout), transport=transport) as client:

        async def timed(label: str, method: str, url: str, body: Any = None
                        ) -> dict[str, Any] | None:  # fmt: skip
            nonlocal failures
            started = time.perf_counter()
            try:
                response = await client.request(method, url, json=body,
                                                headers={"x-goog-api-key": key})  # fmt: skip
            except httpx.HTTPError as exc:
                failures += 1
                say(f"{label:<11} {time.perf_counter() - started:6.1f}s  {type(exc).__name__}")
                return None
            seconds = time.perf_counter() - started
            size = f"sent {len(json.dumps(body)) if body else 0:,} B"
            if response.status_code != 200:
                failures += 1
                say(f"{label:<11} {seconds:6.1f}s  HTTP {response.status_code}  {size}  "
                    f"{response.text[:200]}")  # fmt: skip
                return None
            data: dict[str, Any] = response.json()
            extra = "" if method == "GET" else f"  {_usage(data)}  {_what(data)}"
            say(f"{label:<11} {seconds:6.1f}s  HTTP 200  {size}{extra}")
            return data

        await timed("0 network", "GET", f"{BASE_URL}/models/{llm.model}")
        url = f"{BASE_URL}/models/{llm.model}:generateContent"
        tiny = llm.body(system="Reply with the single word OK.", turns=[Turn("user", "ping")],
                        tools=[], temperature=temperature, max_output_tokens=64,
                        json_output=False)  # fmt: skip
        await timed("1 tiny", "POST", url, tiny)

        notes = TurnNotes(
            lead_state="new_enquiry",
            qualified=False,
            missing=list(config.lead_scoring.qualified_requires),
            known={},
        )
        system = build_system_prompt(config, Scripts(config), language="en",
                                     channel=Channel.WEB_CHAT, notes=notes)  # fmt: skip
        turns = [Turn("user", CUSTOMER)]

        def brain_body(turns: list[Turn]) -> dict[str, Any]:
            return llm.body(system=system, turns=turns, tools=tool_specs(),
                            temperature=temperature, max_output_tokens=REPLY_MAX_TOKENS,
                            json_output=False)  # fmt: skip

        first = await timed("2 brain", "POST", url, brain_body(turns))
        if first is None:
            return 1
        result = GeminiLLM.parse(first)
        if not result.tool_calls:
            say("3 brain+tool skipped: step 2 answered without calling a tool")
        else:
            results = [ToolResult(c.id, c.name, FAKE_RESULTS.get(c.name, OTHER_RESULT))
                       for c in result.tool_calls]  # fmt: skip
            turns += [Turn("assistant", result.text, list(result.tool_calls), raw=result.raw),
                      Turn("tool", tool_results=results)]  # fmt: skip
            await timed("3 brain+tool", "POST", url, brain_body(turns))
    say(
        "If 0 and 1 are slow, look at the network; if they are fast and 2 or 3 are slow, "
        "the request is the problem."
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time Gemini requests: network vs request.")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds per request")
    parser.add_argument("--thinking", choices=["low", "medium", "high", "default"],
                        help="override llm.thinking_level ('default' sends none)")  # fmt: skip
    parser.add_argument("--temperature", type=float, help="override llm.temperature")
    parser.add_argument("--model", help="try another Gemini model, e.g. gemini-3.6-flash")
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
