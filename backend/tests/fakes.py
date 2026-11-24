"""Test doubles. ScriptedLLM replays prepared responses so brain tests never need a live LLM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.providers.llm import ChatResult, ToolCall, ToolSpec, Turn, Usage

Step = ChatResult | Exception | Callable[[str, list[Turn], list[ToolSpec]], ChatResult]


def reply(text: str, tokens: tuple[int, int] = (100, 20)) -> ChatResult:
    return ChatResult(text, [], {"role": "model", "parts": [{"text": text}]}, Usage(*tokens))


def call(name: str, **args: Any) -> ChatResult:
    return calls((name, args))


def calls(*items: tuple[str, dict[str, Any]]) -> ChatResult:
    tool_calls = [ToolCall(f"call-{i}-{n}", n, a) for i, (n, a) in enumerate(items)]
    return ChatResult("", tool_calls, {"role": "model", "parts": []}, Usage(120, 10))


@dataclass
class Request:
    system: str
    turns: list[Turn]
    tools: list[ToolSpec]


@dataclass
class ScriptedLLM:
    steps: list[Step]
    provider: str = "scripted"
    model: str = "scripted-test"
    requests: list[Request] = field(default_factory=list)

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
        self.requests.append(Request(system, list(turns), list(tools)))
        if not self.steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(system, turns, tools)
        return step

    def tool_results(self, index: int = -1) -> dict[str, dict[str, Any]]:
        """Tool results the brain sent in request ``index`` (by tool name)."""
        turn = self.requests[index].turns[-1]
        return {r.name: r.content for r in turn.tool_results}
