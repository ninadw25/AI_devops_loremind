"""
infra/embeddings/nvidia.py
──────────────────────────
NVIDIA NeMo Retriever EmbeddingProvider.

Uses the OpenAI-compatible API at integrate.api.nvidia.com.
Returns 2048-dim L2-normalized vectors.
"""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from core.exceptions import EmbeddingError
from core.logging import get_logger
from core.observability import lf, observe
from core.settings import Settings

log = get_logger(__name__)


class NvidiaEmbeddingProvider:
    name = "nvidia"

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
    def from_settings(cls, s: Settings) -> "NvidiaEmbeddingProvider":
        if not s.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required for embedding_provider=nvidia")
        return cls(
            api_key=s.nvidia_api_key,
            base_url=s.nvidia_base_url,
            model=s.embedding_model,
            dim=s.embedding_dim,
        )

    @observe(name="embed-query")
    def embed(self, text: str) -> list[float]:
        try:
            resp = self._client.embeddings.create(
                input=[text],
                model=self.model,
                encoding_format="float",
                extra_body={"input_type": "query", "truncate": "NONE"},
            )
            vec = np.array(resp.data[0].embedding, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            result = vec.tolist() if norm == 0.0 else (vec / norm).tolist()
            lf.update_current_span(
                input={"text": text[:300]},
                output={"dim": len(result), "ok": True},
                metadata={"model": self.model},
            )
            return result
        except Exception as exc:
            lf.update_current_span(
                input={"text": text[:300]},
                output={"ok": False, "error": str(exc)},
                metadata={"model": self.model},
            )
            raise EmbeddingError(f"NVIDIA embed failed: {exc}") from exc
