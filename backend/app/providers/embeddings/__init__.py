"""Embeddings adapters. Business logic depends on ``Embedder``, never on a vendor SDK."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Literal, Protocol

from app.core.config import ClientConfig
from app.core.enums import EMBEDDING_DIMENSIONS
from app.core.settings import Settings

EmbedKind = Literal["document", "query"]


class EmbeddingsNotConfigured(Exception):
    """The configured provider cannot be used (for example, its API key is missing)."""


class EmbeddingsError(Exception):
    """The provider failed or returned something unusable."""


class Embedder(Protocol):
    provider: str
    model: str
    dimensions: int

    async def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]: ...


def l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise EmbeddingsError("provider returned a zero vector")
    return [v / norm for v in vector]


def check_vectors(vectors: list[list[float]], expected: int, count: int) -> list[list[float]]:
    if len(vectors) != count:
        raise EmbeddingsError(f"expected {count} embeddings, got {len(vectors)}")
    for v in vectors:
        if len(v) != expected:
            raise EmbeddingsError(f"expected {expected} dimensions, got {len(v)}")
    return [l2_normalise(v) for v in vectors]


def get_embedder(config: ClientConfig, settings: Settings) -> Embedder:
    """Build the embedder named in ``embeddings.provider``."""
    cfg = config.embeddings
    if cfg.dimensions != EMBEDDING_DIMENSIONS:  # also enforced by config validation
        raise EmbeddingsNotConfigured("embedding dimensions must be 768")
    if cfg.provider == "local":
        from app.providers.embeddings.local import HashingEmbedder

        return HashingEmbedder(model=cfg.model)
    if cfg.provider == "gemini":
        from app.providers.embeddings.gemini import GeminiEmbedder

        if settings.gemini_api_key is None:
            raise EmbeddingsNotConfigured("GEMINI_API_KEY is not set in .env")
        return GeminiEmbedder(api_key=settings.gemini_api_key.get_secret_value(), model=cfg.model)
    if cfg.provider == "openai":
        from app.providers.embeddings.openai import OpenAIEmbedder

        if settings.openai_api_key is None:
            raise EmbeddingsNotConfigured("OPENAI_API_KEY is not set in .env")
        return OpenAIEmbedder(api_key=settings.openai_api_key.get_secret_value(), model=cfg.model)
    raise EmbeddingsNotConfigured(f"unknown embeddings provider {cfg.provider!r}")


def lazy_embedder(state: Any, config: ClientConfig, settings: Settings) -> Callable[[], Embedder]:
    """The embedder for live conversations, built on first use.

    Uses ``state.embedder`` when the app holds one (tests inject a fake), otherwise the provider
    from config. Building it can raise ``EmbeddingsNotConfigured`` (for example, no API key);
    the knowledge tool turns that into "the team will confirm".
    """

    def make() -> Embedder:
        injected: Embedder | None = getattr(state, "embedder", None)
        return injected if injected is not None else get_embedder(config, settings)

    return make
