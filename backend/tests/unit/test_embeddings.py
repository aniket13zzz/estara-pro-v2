"""Embeddings adapters: request shapes (as documented), normalisation and failures.

Gemini and OpenAI are exercised with a mocked HTTP transport; they are not verified against the
live APIs here (no keys in the test environment).
"""

from __future__ import annotations

import json
import math

import httpx
import pytest

from app.core.config import ClientConfig
from app.providers.embeddings import (
    EmbeddingsError,
    EmbeddingsNotConfigured,
    get_embedder,
)
from app.providers.embeddings.gemini import GeminiEmbedder
from app.providers.embeddings.local import HashingEmbedder
from app.providers.embeddings.openai import OpenAIEmbedder
from tests.conftest import make_test_settings


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


async def test_local_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder()
    a, b, c = await e.embed(["swimming pool", "swimming pool", "parking"], "document")
    assert len(a) == 768 and a == b and a != c
    assert abs(_norm(a) - 1) < 1e-9


async def test_gemini_request_shape_and_normalisation() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        seen["body"] = json.loads(request.content)
        n = len(seen["body"]["requests"])  # type: ignore[index]
        return httpx.Response(200, json={"embeddings": [{"values": [3.0] + [0.0] * 767}] * n})

    e = GeminiEmbedder("k-123", "gemini-embedding-001", transport=httpx.MockTransport(handler))
    [vec] = await e.embed(["hello"], "query")
    assert seen["url"].endswith("/v1beta/models/gemini-embedding-001:batchEmbedContents")  # type: ignore[union-attr]
    assert seen["key"] == "k-123"
    req = seen["body"]["requests"][0]  # type: ignore[index]
    assert req["model"] == "models/gemini-embedding-001"
    assert req["content"] == {"parts": [{"text": "hello"}]}
    assert req["embedContentConfig"] == {"taskType": "RETRIEVAL_QUERY", "outputDimensionality": 768}
    assert vec[0] == pytest.approx(1.0)  # normalised by the client


async def test_openai_request_shape() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        data = [
            {"embedding": [0.0, 2.0] + [0.0] * 766, "index": 1},
            {"embedding": [2.0] + [0.0] * 767, "index": 0},
        ]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 2}})

    e = OpenAIEmbedder("sk-1", "text-embedding-3-small", transport=httpx.MockTransport(handler))
    first, second = await e.embed(["a", "b"], "document")
    assert seen["auth"] == "Bearer sk-1"
    assert seen["body"] == {
        "input": ["a", "b"],
        "model": "text-embedding-3-small",
        "dimensions": 768,
        "encoding_format": "float",
    }
    assert first[0] == pytest.approx(1.0) and second[1] == pytest.approx(1.0)  # sorted by index


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(200, json={"embeddings": [{"values": [1.0] * 10}]}),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json={"embeddings": [{"values": [0.0] * 768}]}),
    ],
)
async def test_gemini_failures_raise_embeddings_error(response: httpx.Response) -> None:
    e = GeminiEmbedder("k", "m", transport=httpx.MockTransport(lambda r: response))
    with pytest.raises(EmbeddingsError):
        await e.embed(["x"], "document")


def test_openai_ada_cannot_do_768() -> None:
    with pytest.raises(EmbeddingsNotConfigured):
        OpenAIEmbedder("k", "text-embedding-ada-002")


def test_factory_needs_keys(client_config: ClientConfig) -> None:
    settings = make_test_settings("postgresql+psycopg://unused@localhost/x")
    with pytest.raises(EmbeddingsNotConfigured, match="GEMINI_API_KEY"):
        get_embedder(client_config, settings)
    keyed = make_test_settings("postgresql+psycopg://unused@localhost/x", gemini_api_key="g")
    assert get_embedder(client_config, keyed).provider == "gemini"
    local = client_config.model_copy(
        update={"embeddings": client_config.embeddings.model_copy(update={"provider": "local"})}
    )
    assert get_embedder(local, settings).provider == "local"
