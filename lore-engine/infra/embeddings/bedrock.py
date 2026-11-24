"""
infra/embeddings/bedrock.py
────────────────────────────
AWS Bedrock EmbeddingProvider — Cohere Embed v4 via invoke_model.

Auth: AWS_BEARER_TOKEN_BEDROCK env var (same bearer token used by the
chat provider). botocore picks it up automatically — no access-key pair.

Default: cohere.embed-v4:0 (1024-dim) in settings.bedrock_region.

Activate via .env:
    EMBEDDING_PROVIDER=bedrock
    EMBEDDING_MODEL=cohere.embed-v4:0
    EMBEDDING_DIM=1024
"""

from __future__ import annotations

import json
import os

import numpy as np

from core.exceptions import EmbeddingError
from core.settings import Settings


class BedrockEmbeddingProvider:
    name = "bedrock"

    def __init__(self, api_key: str, region: str, model: str, dim: int) -> None:
        self.model = model
        self.dim   = dim
        if api_key:
            os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", api_key)
        import boto3
        self._client = boto3.client("bedrock-runtime", region_name=region)

    @classmethod
    def from_settings(cls, s: Settings) -> "BedrockEmbeddingProvider":
        if not s.bedrock_api_key:
            raise ValueError("BEDROCK_API_KEY is required for embedding_provider=bedrock")
        return cls(
            api_key=s.bedrock_api_key,
            region=s.bedrock_region,
            model=s.embedding_model,
            dim=s.embedding_dim,
        )

    def embed(self, text: str) -> list[float]:
        try:
            body = json.dumps({"texts": [text], "input_type": "search_document"})
            resp = self._client.invoke_model(
                modelId=self.model,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            data = json.loads(resp["body"].read())
            vec = np.array(data["embeddings"][0], dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            return vec.tolist() if norm == 0.0 else (vec / norm).tolist()
        except Exception as exc:
            raise EmbeddingError(f"Bedrock embed failed: {exc}") from exc
