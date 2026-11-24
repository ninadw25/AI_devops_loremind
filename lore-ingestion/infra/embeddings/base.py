"""
infra/embeddings/base.py
────────────────────────
EmbeddingProvider Protocol — the single abstraction every embedding
backend must expose.

Two methods, with two different cost profiles:

  embed_batch(texts)  — one synchronous call, no batching, no retry,
                        no normalization. Returns raw `list[list[float]]`.
                        For callers that need their own checkpointing,
                        rate-limit handling, or in-flight progress
                        reporting (e.g., `pipeline/embed_knowledge.py`
                        with its `--resume` semantics).

  embed_all(texts)    — convenience: loops through `embed_batch` with
                        sensible batching + retry on 429, L2-normalizes
                        the result, returns an np.float32 [N, dim] array.
                        For one-shot callers that just want the
                        finished vectors (e.g.,
                        `builders/post_processor.py:embed_topics`).

`dim` is the embedding dimensionality (used by callers for sanity
checks before writing to Neo4j or numpy on-disk artifacts).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A configured text-embedding backend."""

    #: Short identifier ("nvidia"). Used by logs / the registry.
    name: str

    #: Model identifier passed to the underlying API.
    model: str

    #: Output dimensionality (2048 for NVIDIA NeMo).
    dim: int

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed one batch and return raw rows (no normalization, no retry)."""
        ...

    def embed_all(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        max_retries: int = 5,
    ) -> np.ndarray:
        """Embed all `texts`, batching + retrying + L2-normalizing.

        Returns `np.float32 [N, dim]`. Callers that need progress
        callbacks or per-batch checkpointing should use `embed_batch`
        directly and layer their own loop on top.
        """
        ...
