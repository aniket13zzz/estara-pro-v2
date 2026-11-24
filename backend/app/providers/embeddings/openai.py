"""OpenAI embeddings over REST (``embeddings.provider: openai``).

Shapes follow https://developers.openai.com/api/reference/resources/embeddings/methods/create
(checked 2026-10-12): ``POST https://api.openai.com/v1/embeddings``, ``Authorization: Bearer``,
body ``{"input": [...], "model": ..., "dimensions": 768, "encoding_format": "float"}``,
response ``{"data": [{"embedding": [...], "index": n}], "usage": {...}}``. ``dimensions`` works
only for text-embedding-3 models and later.

Not yet verified against the live API (no key in the build workspace).
"""

from __future__ import annotations

import httpx

from app.core.enums import EMBEDDING_DIMENSIONS
from app.providers.embeddings import (
    EmbeddingsError,
    EmbeddingsNotConfigured,
    EmbedKind,
    check_vectors,
)

URL = "https://api.openai.com/v1/embeddings"
MAX_BATCH = 256


class OpenAIEmbedder:
    provider = "openai"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        if model == "text-embedding-ada-002":
            raise EmbeddingsNotConfigured(
                "text-embedding-ada-002 cannot output 768 dimensions; use text-embedding-3-small"
            )
        self.model = model
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    async def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        vectors: list[list[float]] = []
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
            for start in range(0, len(texts), MAX_BATCH):
                chunk = texts[start : start + MAX_BATCH]
                body = {
                    "input": chunk,
                    "model": self.model,
                    "dimensions": self.dimensions,
                    "encoding_format": "float",
                }
                try:
                    resp = await client.post(URL, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    raise EmbeddingsError(f"OpenAI request failed: {type(exc).__name__}") from exc
                if resp.status_code != 200:
                    raise EmbeddingsError(f"OpenAI returned HTTP {resp.status_code}")
                try:
                    items = sorted(resp.json()["data"], key=lambda d: d["index"])
                    vectors += [[float(v) for v in item["embedding"]] for item in items]
                except (KeyError, TypeError, ValueError) as exc:
                    raise EmbeddingsError("OpenAI response had an unexpected shape") from exc
        return check_vectors(vectors, self.dimensions, len(texts))
