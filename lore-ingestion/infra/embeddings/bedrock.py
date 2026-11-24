"""
infra/embeddings/bedrock.py
────────────────────────────
AWS Bedrock EmbeddingProvider — Cohere Embed v4 via invoke_model.

Auth: bedrock_api_key set as AWS_BEARER_TOKEN_BEDROCK env var (same
bearer token used by the LLM provider). No access-key pair needed.

Default: cohere.embed-v4:0 (1024-dim) in settings.bedrock_region.

Activate via .env:
    EMBEDDING_PROVIDER=bedrock
    EMBEDDING_MODEL=cohere.embed-v4:0
    EMBEDDING_DIM=1024
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from common.config import get_settings
from common.logger import get_logger

log = get_logger("infra.embeddings.bedrock")


class BedrockEmbeddingProvider:
    name = "bedrock"

    def __init__(self) -> None:
        s = get_settings()
        if not s.bedrock_api_key:
            raise ValueError(
                "BEDROCK_API_KEY is not set. Add it to .env to use the bedrock embedding provider."
            )
        if s.bedrock_api_key:
            os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", s.bedrock_api_key)
        import boto3
        self._client = boto3.client("bedrock-runtime", region_name=s.bedrock_region)
        self.model = s.embedding_model
        self.dim = s.embedding_dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        body = json.dumps({"texts": texts, "input_type": "search_document"})
        resp = self._client.invoke_model(
            modelId=self.model,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        data = json.loads(resp["body"].read())
        return data["embeddings"]

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
                    wait = 30 if "throttling" in str(e).lower() else 5
                    log.warning(
                        "Bedrock embedding batch %d-%d retry %d/%d (%s) — sleeping %ds",
                        i, i + len(batch), retries, max_retries, str(e)[:80], wait,
                    )
                    time.sleep(wait)

        arr = np.array(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return arr / norms
