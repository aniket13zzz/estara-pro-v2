"""Gemini embeddings over REST (``embeddings.provider: gemini``).

Request and response shapes follow https://ai.google.dev/api/embeddings (checked 2026-10-12):
``POST /v1beta/models/{model}:batchEmbedContents`` with header ``x-goog-api-key``, body
``{"requests": [{"model": "models/{model}", "content": {"parts": [{"text": ...}]},
"embedContentConfig": {"taskType": ..., "outputDimensionality": 768}}]}``, response
``{"embeddings": [{"values": [...]}]}``.

https://ai.google.dev/gemini-api/docs/embeddings: 768 is a recommended dimension for
gemini-embedding-001, and reduced dimensions must be normalised by the client (done here).

Not yet verified against the live API (no key in the build workspace).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.enums import EMBEDDING_DIMENSIONS
from app.providers.embeddings import EmbeddingsError, EmbedKind, check_vectors

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
MAX_BATCH = 100
TASK_TYPES = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}


class GeminiEmbedder:
    provider = "gemini"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.model = model.removeprefix("models/")
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    def _body(self, texts: list[str], kind: EmbedKind) -> dict[str, Any]:
        return {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": text}]},
                    "embedContentConfig": {
                        "taskType": TASK_TYPES[kind],
                        "outputDimensionality": self.dimensions,
                    },
                }
                for text in texts
            ]
        }

    async def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        vectors: list[list[float]] = []
        url = f"{BASE_URL}/models/{self.model}:batchEmbedContents"
        headers = {"x-goog-api-key": self._api_key}
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            for start in range(0, len(texts), MAX_BATCH):
                chunk = texts[start : start + MAX_BATCH]
                try:
                    resp = await client.post(url, json=self._body(chunk, kind), headers=headers)
                except httpx.HTTPError as exc:
                    raise EmbeddingsError(f"Gemini request failed: {type(exc).__name__}") from exc
                if resp.status_code != 200:
                    raise EmbeddingsError(f"Gemini returned HTTP {resp.status_code}")
                try:
                    items = resp.json()["embeddings"]
                    vectors += [[float(v) for v in item["values"]] for item in items]
                except (KeyError, TypeError, ValueError) as exc:
                    raise EmbeddingsError("Gemini response had an unexpected shape") from exc
        return check_vectors(vectors, self.dimensions, len(texts))
