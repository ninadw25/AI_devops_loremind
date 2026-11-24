"""
infra/embeddings/openai.py
──────────────────────────
OpenAI EmbeddingProvider (text-embedding-3-small, text-embedding-3-large).

Returns L2-normalized vectors. Default dim is 1536 for text-embedding-3-small.
"""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from core.exceptions import EmbeddingError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dim: int,
    ) -> None:
        self.model = model
        self.dim   = dim
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenAIEmbeddingProvider":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for embedding_provider=openai")
        return cls(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            model=s.embedding_model,
            dim=s.embedding_dim,
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
            raise EmbeddingError(f"OpenAI embed failed: {exc}") from exc
