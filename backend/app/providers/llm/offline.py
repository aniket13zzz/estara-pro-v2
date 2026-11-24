"""OFFLINE STAND-IN, NOT A LANGUAGE MODEL. For plumbing checks only.

Used by ``make chat --llm offline`` and the eval runner's ``--llm offline`` smoke test when no
LLM key or network is available. It never writes its own sentences: it calls the backend tools
in a fixed order and replies with the tool results' spoken strings, prefixed with
``[offline]``. That exercises language detection, extraction, matching, slots and the visit
request end to end, in both languages, but says nothing about how a real model behaves.
Its output must never be shown to a customer or reported as an eval result.
"""

from __future__ import annotations

from typing import Any

from app.providers.llm import ChatResult, ToolCall, ToolSpec, Turn, Usage

PREFIX = "[offline]"


class OfflineLLM:
    provider = "offline"
    model = "offline-plumbing-check"

    def __init__(self) -> None:
        self._counter = 0

    def _call(self, name: str, **args: Any) -> ToolCall:
        self._counter += 1
        return ToolCall(id=f"offline-{self._counter}", name=name, arguments=args)

    async def chat(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[ToolSpec],
        temperature: float,
        max_output_tokens: int = 1024,
        json_output: bool = False,
    ) -> ChatResult:
        names = {t.name for t in tools}
        last = turns[-1] if turns else None
        if last is not None and last.role == "tool":
            return self._after_tools(turns, names)
        calls = [self._call("evaluate_lead")] if "evaluate_lead" in names else []
        return ChatResult("", calls, None, Usage())

    def _after_tools(self, turns: list[Turn], names: set[str]) -> ChatResult:
        # Every tool result since the customer's last message, oldest first.
        results: dict[str, dict[str, Any]] = {}
        for turn in reversed(turns):
            if turn.role == "user":
                break
            for r in turn.tool_results:
                results.setdefault(r.name, r.content)
        latest = {r.name for r in turns[-1].tool_results}
        evaluation = results.get("evaluate_lead", {})
        if "evaluate_lead" in latest and evaluation.get("qualified"):
            if "search_properties" in names:
                return ChatResult("", [self._call("search_properties")], None, Usage())
        if "search_properties" in latest:
            matches = results["search_properties"].get("matches") or []
            if matches and "get_visit_slots" in names:
                code = matches[0]["project_code"]
                return ChatResult("", [self._call("get_visit_slots", project_code=code)], None,
                                  Usage())  # fmt: skip
        order = ["search_properties", "get_visit_slots"]
        spoken = [s for name in order if name in results for s in _spoken(results[name])]
        if not spoken and evaluation.get("missing"):
            spoken = ["missing: " + ", ".join(evaluation["missing"])]
        return ChatResult(" ".join([PREFIX, *spoken]) if spoken else f"{PREFIX} (no tool data)",
                          [], None, Usage())  # fmt: skip


def _spoken(content: dict[str, Any]) -> list[str]:
    out: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"say", "reason", "slot"} and isinstance(item, str):
                    out.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(content)
    return out
