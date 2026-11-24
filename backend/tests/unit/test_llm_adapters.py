"""LLM adapters against mocked transports: request shapes as documented, parsing, thought
signatures, errors, retries with backoff, extraction and usage cost. No live calls."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import ClientConfig
from app.providers.llm import (
    ChatResult,
    InstrumentedLLM,
    LLMError,
    LLMNotConfigured,
    LLMTransientError,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    cost_usd,
    extract,
    get_llm,
    parse_json_object,
)
from app.providers.llm.claude import ClaudeLLM
from app.providers.llm.gemini import GeminiLLM
from app.providers.llm.openai import OpenAILLM
from tests.conftest import make_test_settings
from tests.fakes import ScriptedLLM, reply

TOOL = ToolSpec(
    "get_visit_slots",
    "Next visit slots",
    {"type": "object", "properties": {"project_code": {"type": "string"}}},
)


def transport(responses: list[httpx.Response], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0)

    return httpx.MockTransport(handler)


def body(request: httpx.Request) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(request.content)
    return data


# ------------------------------------------------------------------------------ Gemini

GEMINI_CALL = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {"name": "get_visit_slots", "args": {"project_code": "SG"}},
                        "thoughtSignature": "sig-A",
                    }
                ],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 7, "thoughtsTokenCount": 30},
    "modelVersion": "gemini-3.8-flash",
}


async def test_gemini_request_shape_and_function_call() -> None:
    seen: list[httpx.Request] = []
    llm = GeminiLLM(
        "key-1",
        "gemini-3.8-flash",
        transport=transport([httpx.Response(200, json=GEMINI_CALL)], seen),
    )
    result = await llm.chat(
        system="be brief",
        turns=[Turn("user", "slots?")],
        tools=[TOOL],
        temperature=0.3,
        max_output_tokens=300,
    )
    req = seen[0]
    assert str(req.url).endswith("/v1beta/models/gemini-3.8-flash:generateContent")
    assert req.headers["x-goog-api-key"] == "key-1"
    sent = body(req)
    assert sent["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert sent["contents"] == [{"role": "user", "parts": [{"text": "slots?"}]}]
    assert sent["tools"][0]["functionDeclarations"][0]["name"] == "get_visit_slots"
    assert sent["generationConfig"] == {"temperature": 0.3, "maxOutputTokens": 300}
    assert result.tool_calls[0].name == "get_visit_slots"
    assert result.tool_calls[0].arguments == {"project_code": "SG"}
    assert result.usage == Usage(50, 37)  # thinking tokens are billed as output


async def test_gemini_echoes_thought_signature_and_sends_function_response() -> None:
    seen: list[httpx.Request] = []
    final = {"candidates": [{"content": {"role": "model", "parts": [{"text": "Saturday"}]}}]}
    llm = GeminiLLM(
        "k",
        "m",
        transport=transport(
            [httpx.Response(200, json=GEMINI_CALL), httpx.Response(200, json=final)], seen
        ),
    )
    first = await llm.chat(system="s", turns=[Turn("user", "slots?")], tools=[TOOL], temperature=0)
    call = first.tool_calls[0]
    turns = [
        Turn("user", "slots?"),
        Turn("assistant", raw=first.raw, tool_calls=first.tool_calls),
        Turn("tool", tool_results=[ToolResult(call.id, call.name, {"slots": ["sat 11"]})]),
    ]
    second = await llm.chat(system="s", turns=turns, tools=[TOOL], temperature=0)
    contents = body(seen[1])["contents"]
    assert contents[1]["parts"][0]["thoughtSignature"] == "sig-A"  # exactly as received
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "get_visit_slots", "response": {"slots": ["sat 11"]}}}
        ],
    }
    assert second.text == "Saturday"


async def test_gemini_ignores_thought_text_and_reports_blocks() -> None:
    data = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"text": "thinking...", "thought": True}, {"text": "Hello"}],
                }
            }
        ]
    }
    assert GeminiLLM.parse(data).text == "Hello"
    with pytest.raises(LLMError, match="SAFETY"):
        GeminiLLM.parse({"promptFeedback": {"blockReason": "SAFETY"}})


async def test_gemini_json_mode() -> None:
    llm = GeminiLLM("k", "m")
    sent = llm.body(
        system="s", turns=[], tools=[], temperature=0, max_output_tokens=10, json_output=True
    )
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    assert "tools" not in sent


async def test_gemini_leaves_out_parameters_for_tools_without_arguments() -> None:
    from app.brain.tools import tool_specs

    sent = GeminiLLM("k", "m").body(
        system="s", turns=[], tools=tool_specs(), temperature=0, max_output_tokens=10,
        json_output=False,
    )  # fmt: skip
    declarations = {d["name"]: d for d in sent["tools"][0]["functionDeclarations"]}
    assert len(declarations) == 12
    assert "parameters" not in declarations["evaluate_lead"]
    assert declarations["get_commercial_info"]["parameters"]["required"] == [
        "project_code", "fact_type",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("status", "transient"), [(429, True), (503, True), (400, False), (401, False)]
)
async def test_http_errors_are_classified(status: int, transient: bool) -> None:
    llm = GeminiLLM("k", "m", transport=transport([httpx.Response(status, json={})], []))
    with pytest.raises(LLMError) as exc:
        await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    assert exc.value.transient is transient


async def test_timeouts_are_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    llm = GeminiLLM("k", "m", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMTransientError, match="ReadTimeout"):
        await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)


# ------------------------------------------------------------------------------ Claude


async def test_claude_request_and_tool_round_trip() -> None:
    seen: list[httpx.Request] = []
    tool_use = {
        "content": [
            {"type": "text", "text": "Checking."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_visit_slots",
                "input": {"project_code": "SG"},
            },
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 40, "output_tokens": 9},
    }
    llm = ClaudeLLM(
        "ak", "claude-model", transport=transport([httpx.Response(200, json=tool_use)], seen)
    )
    result = await llm.chat(system="sys", turns=[Turn("user", "hi")], tools=[TOOL], temperature=0.2)
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "ak"
    assert req.headers["anthropic-version"] == "2023-06-01"
    sent = body(req)
    assert sent["system"] == "sys" and sent["messages"] == [{"role": "user", "content": "hi"}]
    assert sent["tools"][0]["input_schema"]["properties"]["project_code"] == {"type": "string"}
    assert result.tool_calls == [ToolCall("toolu_1", "get_visit_slots", {"project_code": "SG"})]
    assert result.usage == Usage(40, 9)
    tool_turn = Turn("tool", tool_results=[ToolResult("toolu_1", "get_visit_slots", {"x": 1})])
    msg = ClaudeLLM._message(tool_turn)
    assert msg["role"] == "user" and msg["content"][0]["type"] == "tool_result"
    assert msg["content"][0]["tool_use_id"] == "toolu_1"
    assert ClaudeLLM._message(Turn("assistant", raw=result.raw))["content"] == tool_use["content"]


# ------------------------------------------------------------------------------ OpenAI


async def test_openai_responses_api_shapes() -> None:
    seen: list[httpx.Request] = []
    out = {
        "output": [
            {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "get_visit_slots",
                "arguments": '{"project_code": "SG"}',
            },
        ],
        "status": "completed",
        "usage": {"input_tokens": 30, "output_tokens": 5},
    }
    llm = OpenAILLM("ok", "gpt-model", transport=transport([httpx.Response(200, json=out)], seen))
    result = await llm.chat(system="sys", turns=[Turn("user", "hi")], tools=[TOOL], temperature=0.3)
    req = seen[0]
    assert str(req.url) == "https://api.openai.com/v1/responses"
    assert req.headers["authorization"] == "Bearer ok"
    sent = body(req)
    assert sent["instructions"] == "sys" and sent["store"] is False
    assert sent["input"] == [{"role": "user", "content": "hi"}]
    assert sent["tools"][0] == {
        "type": "function",
        "name": "get_visit_slots",
        "description": "Next visit slots",
        "parameters": TOOL.parameters,
    }
    assert result.tool_calls == [ToolCall("call_1", "get_visit_slots", {"project_code": "SG"})]
    items = OpenAILLM._items(Turn("assistant", raw=result.raw))
    assert items[0]["type"] == "reasoning"  # reasoning items echoed back as received
    outputs = OpenAILLM._items(Turn("tool", tool_results=[ToolResult("call_1", "x", {"a": 1})]))
    assert outputs == [{"type": "function_call_output", "call_id": "call_1", "output": '{"a": 1}'}]


async def test_openai_retries_without_temperature_when_rejected() -> None:
    seen: list[httpx.Request] = []
    ok = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Hi"}]}]}
    llm = OpenAILLM(
        "k",
        "m",
        transport=transport(
            [
                httpx.Response(400, text="Unsupported parameter: 'temperature'"),
                httpx.Response(200, json=ok),
            ],
            seen,
        ),
    )
    result = await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0.3)
    assert result.text == "Hi"
    assert "temperature" in body(seen[0]) and "temperature" not in body(seen[1])


# ------------------------------------------------------------------- retries and usage


async def test_retries_transient_errors_with_exponential_backoff() -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    inner = ScriptedLLM([LLMTransientError("503"), LLMTransientError("429"), reply("ok")])
    recorded: list[tuple[ChatResult, str]] = []

    async def recorder(result: ChatResult, purpose: str) -> None:
        recorded.append((result, purpose))

    llm = InstrumentedLLM(
        inner, max_attempts=3, base_delay=0.5, recorder=recorder, sleep=fake_sleep
    )
    result = await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    assert result.text == "ok" and llm.attempts_used == 3
    assert len(delays) == 2 and 0.5 <= delays[0] < 0.7 and 1.0 <= delays[1] < 1.3
    assert len(recorded) == 1 and recorded[0][1] == "chat"


async def test_gives_up_after_max_attempts_and_never_retries_permanent_errors() -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    llm = InstrumentedLLM(ScriptedLLM([LLMTransientError("a")] * 3), max_attempts=3, sleep=no_sleep)
    with pytest.raises(LLMTransientError):
        await llm.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    permanent = InstrumentedLLM(ScriptedLLM([LLMError("400"), reply("never")]), sleep=no_sleep)
    with pytest.raises(LLMError, match="400"):
        await permanent.chat(system="s", turns=[Turn("user", "x")], tools=[], temperature=0)
    assert permanent.attempts_used == 1


class Budget(BaseModel):
    budget_max_inr: int
    locations: list[str]


async def test_extract_validates_and_retries_once() -> None:
    inner = ScriptedLLM(
        [
            reply("Sure! {not json"),
            reply('```json\n{"budget_max_inr": 8000000, "locations": ["New Town"]}\n```'),
        ]
    )
    result = await extract(
        InstrumentedLLM(inner), instructions="Extract.", text="80 lakh", schema=Budget
    )
    assert result == Budget(budget_max_inr=8_000_000, locations=["New Town"])
    assert "JSON Schema" in inner.requests[0].system
    with pytest.raises(ValueError):
        parse_json_object("no braces here")


def test_cost_uses_config_pricing(client_config: ClientConfig) -> None:
    # Sample config: 0.75 USD per million input tokens, 3.75 per million output tokens.
    assert cost_usd(client_config, Usage(1_000_000, 200_000)) == pytest.approx(1.5)
    unpriced = client_config.model_copy(
        update={"llm": client_config.llm.model_copy(update={"pricing": None})}
    )
    assert cost_usd(unpriced, Usage(10, 10)) is None


def test_factory_needs_the_providers_key(client_config: ClientConfig) -> None:
    settings = make_test_settings("postgresql+psycopg://unused@localhost/x")
    with pytest.raises(LLMNotConfigured, match="GEMINI_API_KEY"):
        get_llm(client_config, settings)
    keyed = make_test_settings("postgresql+psycopg://unused@localhost/x", gemini_api_key="g")
    llm = get_llm(client_config, keyed)
    assert llm.provider == "gemini" and llm.model == "gemini-3.6-flash"
    for provider, key in (("claude", "anthropic_api_key"), ("openai", "openai_api_key")):
        cfg = client_config.model_copy(
            update={"llm": client_config.llm.model_copy(update={"provider": provider})}
        )
        with pytest.raises(LLMNotConfigured):
            get_llm(cfg, settings)
        assert (
            get_llm(cfg, make_test_settings("postgresql+psycopg://u@l/x", **{key: "k"})).provider
            == provider
        )
