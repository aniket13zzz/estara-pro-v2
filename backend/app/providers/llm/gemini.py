"""Gemini over REST ``generateContent`` (``llm.provider: gemini``).

Shapes from the Gemini API docs, checked 2026-10-13:

- ``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
  with header ``x-goog-api-key`` (https://ai.google.dev/api/generate-content,
  https://ai.google.dev/api/embeddings).
- Body: ``systemInstruction`` (Content), ``contents`` (roles ``user`` / ``model``), ``tools``
  with ``functionDeclarations`` (``name``, ``description``, ``parameters``), ``generationConfig``
  (``temperature``, ``maxOutputTokens``, ``responseMimeType``).
- Response: ``candidates[0].content.parts`` with ``text`` or ``functionCall`` (``name``,
  ``args``, optional ``id``); ``finishReason``; ``usageMetadata`` (``promptTokenCount``,
  ``candidatesTokenCount``, ``thoughtsTokenCount``).
- Function results go back as a ``user`` turn of ``functionResponse`` parts (``name``,
  ``response``, and ``id`` when the call had one), after all the calls of that step.
- Thought signatures (https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures):
  Gemini 3 models put ``thoughtSignature`` on the first ``functionCall`` part and reject the
  next request with HTTP 400 if it is missing. The model's content is therefore sent back
  exactly as received (``Turn.raw``).
- Billing counts thinking tokens as output (https://ai.google.dev/gemini-api/docs/pricing), so
  ``output_tokens`` = candidates + thoughts.

Not verified against the live API from the build workspace (network policy blocks the host);
request and response handling is tested with a mocked transport.
"""

from __future__ import annotations

import uuid
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

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiLLM:
    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 25.0,
        thinking_level: str | None = None,
    ) -> None:
        self.model = model.removeprefix("models/")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout
        self.thinking_level = thinking_level

    @staticmethod
    def _content(turn: Turn) -> dict[str, Any]:
        if turn.role == "user":
            return {"role": "user", "parts": [{"text": turn.text}]}
        if turn.role == "assistant":
            if turn.raw is not None:
                return dict(turn.raw)  # exactly as received: keeps thoughtSignature
            return {"role": "model", "parts": [{"text": turn.text}]}
        parts = []
        for r in turn.tool_results:
            response: dict[str, Any] = {"name": r.name, "response": r.content}
            if not r.call_id.startswith("local-"):
                response["id"] = r.call_id
            parts.append({"functionResponse": response})
        return {"role": "user", "parts": parts}

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
        config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_output_tokens}
        if json_output:
            config["responseMimeType"] = "application/json"
        if self.thinking_level:
            # https://ai.google.dev/gemini-api/docs/generate-content/text-generation (REST
            # example with gemini-3.8-flash), checked 2026-11-02.
            config["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [self._content(t) for t in turns],
            "generationConfig": config,
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        # ``parameters`` is optional; a tool without arguments leaves it out.
                        {"name": t.name, "description": t.description}
                        | ({"parameters": t.parameters} if t.parameters.get("properties") else {})
                        for t in tools
                    ]
                }
            ]
        return body

    @staticmethod
    def parse(data: dict[str, Any]) -> ChatResult:
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason})")
        candidate = candidates[0]
        content = candidate.get("content") or {"role": "model", "parts": []}
        texts: list[str] = []
        calls: list[ToolCall] = []
        for part in content.get("parts") or []:
            if "functionCall" in part:
                fc = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=fc.get("id") or f"local-{uuid.uuid4().hex[:12]}",
                        name=fc["name"],
                        arguments=dict(fc.get("args") or {}),
                    )
                )
            elif "text" in part and not part.get("thought"):
                texts.append(part["text"])
        meta = data.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=int(meta.get("promptTokenCount") or 0),
            output_tokens=int(meta.get("candidatesTokenCount") or 0)
            + int(meta.get("thoughtsTokenCount") or 0),
        )
        return ChatResult(
            text="".join(texts),
            tool_calls=calls,
            raw={"role": content.get("role", "model"), "parts": content.get("parts") or []},
            usage=usage,
            finish_reason=str(candidate.get("finishReason", "")),
            model=str(data.get("modelVersion", "")),
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
        url = f"{BASE_URL}/models/{self.model}:generateContent"
        body = self.body(
            system=system, turns=turns, tools=tools, temperature=temperature,
            max_output_tokens=max_output_tokens, json_output=json_output,
        )  # fmt: skip
        async with httpx.AsyncClient(
            transport=self._transport, timeout=http_timeout(self._timeout)
        ) as client:
            try:
                response = await client.post(
                    url, json=body, headers={"x-goog-api-key": self._api_key}
                )
            except httpx.HTTPError as exc:
                raise transport_error("Gemini", exc) from exc
        if response.status_code != 200:
            raise http_error("Gemini", response)
        try:
            return self.parse(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMError("Gemini response had an unexpected shape") from exc
