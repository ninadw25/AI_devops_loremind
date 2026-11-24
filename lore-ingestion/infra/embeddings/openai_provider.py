"""
infra/embeddings/openai_provider.py
────────────────────────────────────
OpenAI text-embedding provider.

Default model: text-embedding-3-small (1536-dim, cheap, fast).
Set EMBEDDING_MODEL=text-embedding-3-large for higher accuracy (3072-dim).

Activate via .env:
    EMBEDDING_PROVIDER=openai
    EMBEDDING_MODEL=text-embedding-3-small
    EMBEDDING_DIM=1536
    OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import time

import numpy as np
from openai import OpenAI

from common.config import get_settings
from common.logger import get_logger

log = get_logger("infra.embeddings.openai")


class OpenAIEmbeddingProvider:
    """Embedding provider wired to the OpenAI embeddings API."""

    name = "openai"

    def __init__(self) -> None:
        s = get_settings()
        if not s.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Add it to .env to use the openai embedding provider."
            )
        self._client = OpenAI(api_key=s.openai_api_key)
        self.model = s.embedding_model
        self.dim = s.embedding_dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(
            model=self.model,
            input=texts,
            encoding_format="float",
        )
        return [item.embedding for item in resp.data]

    def embed_all(
        self,
        texts: list[str],
        *,
        batch_size: int = 32,
        max_retries: int = 5,
    ) -> np.ndarray:
        rows: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            retries = 0
            while True:
                try:
                    rows.extend(self.embed_batch(batch))
                    break
                except Exception as e:
                    retries += 1
                    if retries > max_retries:
                        raise
                    wait = 30 if ("429" in str(e) or "rate" in str(e).lower()) else 5
                    log.warning(
                        "OpenAI embedding batch %d-%d retry %d/%d (%s) — sleeping %ds",
                        i, i + len(batch), retries, max_retries, str(e)[:80], wait,
                    )
                    time.sleep(wait)

        arr = np.array(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return arr / norms
