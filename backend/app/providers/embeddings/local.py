"""Offline hashing embedder (``embeddings.provider: local``).

Feature hashing of words and character trigrams into 768 dimensions, L2-normalised. It needs no
network or key and is deterministic, so tests and keyless development work. It matches on shared
words, not meaning: use Gemini or OpenAI for real semantic search.
"""

from __future__ import annotations

import hashlib
import re

from app.core.enums import EMBEDDING_DIMENSIONS
from app.providers.embeddings import EmbedKind, l2_normalise

TOKEN = re.compile(r"\w+", re.UNICODE)


class HashingEmbedder:
    provider = "local"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self, model: str = "hashing-v1") -> None:
        self.model = model

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        words = TOKEN.findall(text.lower())
        features = list(words)
        for w in words:
            padded = f"#{w}#"
            features += [padded[i : i + 3] for i in range(max(1, len(padded) - 2))]
        for feature in features or ["<empty>"]:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign * (2.0 if feature in words else 1.0)
        if not any(vec):
            vec[0] = 1.0
        return l2_normalise(vec)

    async def embed(self, texts: list[str], kind: EmbedKind) -> list[list[float]]:
        return [self._vector(t) for t in texts]
