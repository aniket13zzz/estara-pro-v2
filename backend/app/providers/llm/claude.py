"""Claude over the Messages API (``llm.provider: claude``).

Shapes from the Claude docs, checked 2026-10-13
(https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools and
.../handle-tool-calls):

- ``POST https://api.anthropic.com/v1/messages``; body ``model``, ``max_tokens``, ``system``,
  ``messages``, ``tools`` (``name``, ``description``, ``input_schema``), ``temperature``.
- Response content blocks: ``text`` and ``tool_use`` (``id``, ``name``, ``input``);
  ``stop_reason`` ``tool_use`` when the model wants a tool.
- Results: a ``user`` message whose content starts with ``tool_result`` blocks (``tool_use_id``,
  ``content``, ``is_error``), immediately after the assistant's ``tool_use`` message.
- Headers ``x-api-key`` and ``anthropic-version: 2023-06-01`` and the ``usage`` fields
  ``input_tokens`` / ``output_tokens`` were not on those pages; they follow the long-standing
  Messages API and must be confirmed with a real key.

Lightly tested: request and response handling with a mocked transport only (no key here).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.providers.llm import (
    ChatResult,
    LLMError,
    ToolCall,
    ToolSpec,
    Turn,
    Usage,
    http_error,
    http_timeout,
    transport_error,
)

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


class ClaudeLLM:
    provider = "claude"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 25.0,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    @staticmethod
    def _message(turn: Turn) -> dict[str, Any]:
        if turn.role == "user":
            return {"role": "user", "content": turn.text}
        if turn.role == "assistant":
            if turn.raw is not None:
                return {"role": "assistant", "content": turn.raw}
            return {"role": "assistant", "content": turn.text}
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r.call_id,
                    "content": json.dumps(r.content, ensure_ascii=False),
                    "is_error": r.content.get("status") == "error",
                }
                for r in turn.tool_results
            ],
        }

    def body(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[ToolSpec],
        temperature: float,
        max_output_tokens: int,
        json_output: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "system": system,
            "temperature": temperature,
            "messages": [self._message(t) for t in turns],
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        return body

    @staticmethod
    def parse(data: dict[str, Any]) -> ChatResult:
        blocks = data.get("content") or []
        texts = [b["text"] for b in blocks if b.get("type") == "text"]
        calls = [
            ToolCall(id=b["id"], name=b["name"], arguments=dict(b.get("input") or {}))
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        usage = data.get("usage") or {}
        return ChatResult(
            text="".join(texts),
            tool_calls=calls,
            raw=blocks,
            usage=Usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)),
            finish_reason=str(data.get("stop_reason", "")),
            model=str(data.get("model", "")),
        )

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
        body = self.body(
            system=system, turns=turns, tools=tools, temperature=temperature,
            max_output_tokens=max_output_tokens, json_output=json_output,
        )  # fmt: skip
        headers = {"x-api-key": self._api_key, "anthropic-version": API_VERSION}
        async with httpx.AsyncClient(
            transport=self._transport, timeout=http_timeout(self._timeout)
        ) as client:
            try:
                response = await client.post(URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise transport_error("Claude", exc) from exc
        if response.status_code != 200:
            raise http_error("Claude", response)
        try:
            return self.parse(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("Claude response had an unexpected shape") from exc
