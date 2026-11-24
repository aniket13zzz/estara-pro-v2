"""Gemini thinking level and temperature from config, and the latency probe."""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx
import pytest

from app.brain import probe
from app.core.config import ClientConfig, parse_config
from app.providers.llm import Turn
from app.providers.llm.gemini import GeminiLLM
from tests.conftest import CLIENT_DIR


def test_sample_config_follows_googles_gemini_3_guidance(client_config: ClientConfig) -> None:
    assert client_config.llm.temperature == 1.0  # lower values may loop (Gemini 3 guide)
    assert client_config.llm.thinking_level == "low"
    assert client_config.llm.model == "gemini-3.6-flash"  # ADR 0011


def test_thinking_level_is_validated() -> None:
    import yaml

    data = yaml.safe_load((CLIENT_DIR / "client.config.yaml").read_text(encoding="utf-8"))
    data["llm"]["thinking_level"] = "extreme"
    with pytest.raises(Exception, match="thinking_level"):
        parse_config(data, CLIENT_DIR)
    data["llm"]["thinking_level"] = None
    assert parse_config(data, CLIENT_DIR).llm.thinking_level is None


def _body(llm: GeminiLLM) -> dict[str, Any]:
    return llm.body(system="s", turns=[Turn("user", "x")], tools=[], temperature=1.0,
                    max_output_tokens=100, json_output=False)  # fmt: skip


def test_gemini_sends_thinking_level_only_when_set() -> None:
    low = _body(GeminiLLM("k", "gemini-3.8-flash", thinking_level="low"))
    assert low["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert "thinkingConfig" not in _body(GeminiLLM("k", "gemini-3.8-flash"))["generationConfig"]


def test_get_llm_passes_the_thinking_level(client_config: ClientConfig) -> None:
    from app.providers.llm import get_llm
    from tests.conftest import make_test_settings

    settings = make_test_settings("postgresql+psycopg://x/y", gemini_api_key="k")
    llm = get_llm(client_config, settings)
    assert isinstance(llm, GeminiLLM) and llm.thinking_level == "low"


def _gemini(parts: list[dict[str, Any]], thoughts: int = 0) -> httpx.Response:
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 2100, "candidatesTokenCount": 12,
                          "thoughtsTokenCount": thoughts},
    })  # fmt: skip


async def test_probe_times_network_tiny_brain_and_tool_round(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SESSION_SECRET", "probe-test-secret-" + "x" * 32)
    seen: list[httpx.Request] = []
    call = {"functionCall": {"name": "save_requirements", "args": {"bhk": [2]}},
            "thoughtSignature": "sig"}  # fmt: skip
    replies = [
        httpx.Response(200, json={"name": "models/gemini-3.8-flash"}),
        _gemini([{"text": "OK"}]),
        _gemini([call], thoughts=180),
        _gemini([{"text": "Great, shall I share visit slots?"}], thoughts=90),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return replies.pop(0)

    args = argparse.Namespace(timeout=120.0, thinking=None, temperature=None, model=None)
    assert await probe.main_async(args, transport=httpx.MockTransport(handler)) == 0
    out = capsys.readouterr().out
    assert "temperature 1, thinking low" in out
    for label in ("0 network", "1 tiny", "2 brain", "3 brain+tool"):
        assert label in out
    assert "thinking 180" in out and "tools ['save_requirements']" in out
    assert [r.method for r in seen] == ["GET", "POST", "POST", "POST"]
    brain = json.loads(seen[2].content)
    assert len(brain["tools"][0]["functionDeclarations"]) == 12
    assert brain["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    follow_up = json.loads(seen[3].content)["contents"]
    assert follow_up[1]["parts"][0]["thoughtSignature"] == "sig"  # echoed as received
    assert "functionResponse" in follow_up[2]["parts"][0]
    assert all(r.headers["x-goog-api-key"] == "test-key" for r in seen)


async def test_probe_reports_timeouts_and_overrides(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("SESSION_SECRET", "probe-test-secret-" + "x" * 32)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={})
        raise httpx.ReadTimeout("slow", request=request)

    args = argparse.Namespace(
        timeout=5.0, thinking="default", temperature=0.3, model="gemini-3.6-flash"
    )
    assert await probe.main_async(args, transport=httpx.MockTransport(handler)) == 1
    out = capsys.readouterr().out
    assert "model gemini-3.6-flash, temperature 0.3, thinking model default" in out
    assert "1 tiny" in out and "ReadTimeout" in out
