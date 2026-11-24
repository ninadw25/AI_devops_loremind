"""
infra/embeddings/nvidia.py
──────────────────────────
NVIDIA NeMo Retriever (`nvidia/llama-3.2-nemoretriever-300m-embed-v1`,
2048-dim) embedding provider.

Wraps the OpenAI-compatible `embeddings.create` endpoint. `embed_batch`
sends the exact `extra_body` the NVIDIA endpoint expects (`input_type`
and `truncate=NONE`); `embed_all` handles retry-on-429 with a 30s
backoff so transient rate caps don't kill long indexing runs.
"""

from __future__ import annotations

import time

import numpy as np
from openai import OpenAI
from openai import APIStatusError

from common.config import get_settings
from common.logger import get_logger

log = get_logger("infra.embeddings.nvidia")


class NvidiaEmbeddingProvider:
    """Embedding provider wired to NVIDIA Build."""

    name = "nvidia"

    def __init__(self) -> None:
        s = get_settings()
        self._client = OpenAI(api_key=s.nvidia_api_key, base_url=s.nvidia_base_url)
        self.model = s.embedding_model
        self.dim = s.embedding_dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """One call to NVIDIA's embeddings endpoint — no retry, no batching."""
        try:
            resp = self._client.embeddings.create(
                input=texts,
                model=self.model,
                encoding_format="float",
                extra_body={"input_type": "query", "truncate": "NONE"},
            )
        except APIStatusError as e:
            detail = ""
            if getattr(e, "response", None) is not None:
                try:
                    payload = e.response.json()
                    detail = str(payload.get("detail", ""))
                except Exception:
                    detail = ""
            if e.status_code == 410:
                raise RuntimeError(
                    "Configured embedding model is unavailable (410 Gone). "
                    f"EMBEDDING_MODEL={self.model!r}. "
                    "Choose a currently available NVIDIA embeddings model. "
                    f"Provider detail: {detail or 'no detail'}"
                ) from e
            raise
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
            batch = texts[i: i + batch_size]
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
                        "NVIDIA embedding batch %d-%d retry %d/%d (%s) — sleeping %ds",
                        i, i + len(batch), retries, max_retries, str(e)[:80], wait,
                    )
                    time.sleep(wait)

        arr = np.array(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return arr / norms
