"""
infra/embeddings/openrouter.py
──────────────────────────────
OpenRouter EmbeddingProvider.

Uses the /embeddings endpoint at openrouter_base_url.
Default model: openai/text-embedding-3-small (1536-dim).
"""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from core.exceptions import EmbeddingError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


class OpenRouterEmbeddingProvider:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dim: int,
        timeout: float,
    ) -> None:
        self.model = model
        self.dim   = dim
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenRouterEmbeddingProvider":
        if not s.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required for embedding_provider=openrouter")
        return cls(
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            model=s.embedding_model,
            dim=s.embedding_dim,
            timeout=s.openrouter_timeout_s,
        )

    def embed(self, text: str) -> list[float]:
        try:
            resp = self._client.embeddings.create(
                input=[text],
                model=self.model,
                encoding_format="float",
            )
            vec = np.array(resp.data[0].embedding, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            return vec.tolist() if norm == 0.0 else (vec / norm).tolist()
        except Exception as exc:
            raise EmbeddingError(f"OpenRouter embed failed: {exc}") from exc
