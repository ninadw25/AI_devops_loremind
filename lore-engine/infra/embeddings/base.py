"""
infra/embeddings/base.py
────────────────────────
EmbeddingProvider Protocol.

Implementations must return L2-normalized float vectors of length `dim`.
Raises EmbeddingError on failure — never returns None.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str   # e.g. "nvidia", "openai", "openrouter"
    model: str  # e.g. "nvidia/llama-3.2-nemoretriever-300m-embed-v1"
    dim: int    # embedding dimensionality, e.g. 2048 or 1536

    def embed(self, text: str) -> list[float]:
        """Return an L2-normalized embedding vector.

        Raises EmbeddingError on API failure.
        Never returns None — callers can depend on receiving a valid vector
        or catching a typed exception.
        """
        ...
