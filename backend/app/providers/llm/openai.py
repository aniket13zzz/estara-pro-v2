"""OpenAI over the Responses API (``llm.provider: openai``).

OpenAI's function-calling guide (checked 2026-10-13,
https://developers.openai.com/api/docs/guides/function-calling) says its newest models require
the Responses API for tool calling, so this adapter uses it rather than Chat Completions.

Shapes from https://developers.openai.com/api/reference/resources/responses/methods/create:

- ``POST https://api.openai.com/v1/responses``, ``Authorization: Bearer``.
- Body: ``model``, ``instructions``, ``input`` (message items ``{role, content}``,
  ``function_call`` items ``{call_id, name, arguments}``, ``function_call_output`` items
  ``{call_id, output}``), flat function ``tools`` (``type``, ``name``, ``description``,
  ``parameters``).
- Response ``output[]``: ``message`` items with ``output_text`` content parts, ``function_call``
  items, ``reasoning`` items. With ``store: false`` reasoning items are sent back as received,
  with ``include: ["reasoning.encrypted_content"]``.
- ``usage.input_tokens`` / ``usage.output_tokens`` and ``max_output_tokens`` / ``temperature``
  were not visible in the fetched excerpt; they must be confirmed with a real key. Some
  reasoning models reject ``temperature``: on such a 400 the call is repeated once without it.

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

URL = "https://api.openai.com/v1/responses"


class OpenAILLM:
    provider = "openai"

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
    def _items(turn: Turn) -> list[dict[str, Any]]:
        if turn.role == "user":
            return [{"role": "user", "content": turn.text}]
        if turn.role == "assistant":
            if turn.raw is not None:
                return list(turn.raw)
            return [{"role": "assistant", "content": turn.text}]
        return [
            {
                "type": "function_call_output",
                "call_id": r.call_id,
                "output": json.dumps(r.content, ensure_ascii=False),
            }
            for r in turn.tool_results
        ]

    def body(
        self,
        *,
        system: str,
        turns: list[Turn],
        tools: list[ToolSpec],
        temperature: float | None,
        max_output_tokens: int,
        json_output: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": [item for t in turns for item in self._items(t)],
            "max_output_tokens": max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [
                {"type": "function", "name": t.name, "description": t.description,
                 "parameters": t.parameters}
                for t in tools
            ]  # fmt: skip
        return body

    @staticmethod
    def parse(data: dict[str, Any]) -> ChatResult:
        output = data.get("output") or []
        texts: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            if item.get("type") == "message":
                texts += [
                    c.get("text", "")
                    for c in item.get("content") or []
                    if c.get("type") == "output_text"
                ]
            elif item.get("type") == "function_call":
                calls.append(
                    ToolCall(
                        id=item["call_id"],
                        name=item["name"],
                        arguments=json.loads(item.get("arguments") or "{}"),
                    )
                )
        usage = data.get("usage") or {}
        return ChatResult(
            text="".join(texts),
            tool_calls=calls,
            raw=output,
            usage=Usage(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)),
            finish_reason=str(data.get("status", "")),
            model=str(data.get("model", "")),
        )

    async def _post(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(
            transport=self._transport, timeout=http_timeout(self._timeout)
        ) as client:
            try:
                return await client.post(URL, json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise transport_error("OpenAI", exc) from exc

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
        response = await self._post(body)
        if response.status_code == 400 and "temperature" in response.text:
            body.pop("temperature", None)
            response = await self._post(body)
        if response.status_code != 200:
            raise http_error("OpenAI", response)
        try:
            return self.parse(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("OpenAI response had an unexpected shape") from exc
