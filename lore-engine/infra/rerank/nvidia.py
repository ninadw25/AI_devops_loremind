"""
infra/rerank/nvidia.py
──────────────────────
Hosted cross-encoder reranker backed by an NVIDIA reranking NIM
(e.g. nvidia/llama-3.2-nv-rerankqa-1b-v2).

Config switch for deployments that don't want model weights in the engine
container. Adds one network round-trip and per-query cost vs. the local
FlashRank default. Uses the NVIDIA ranking endpoint
({nvidia_base_url}/ranking) with the standard NIM payload shape:

    {"model": ..., "query": {"text": q}, "passages": [{"text": p}, ...]}
    → {"rankings": [{"index": i, "logit": score}, ...]}

NOTE: the endpoint shape is validated against NVIDIA's reranking NIM contract;
confirm against the live deployment before cutover (docs/QUERY_PIPELINE_V2.md §7).
"""

from __future__ import annotations

import requests

from core.exceptions import RerankError
from core.logging import get_logger
from core.observability import observe
from core.settings import Settings
from core.types import RankedDoc, RerankDoc

log = get_logger(__name__)


class NvidiaRerankProvider:
    name = "nvidia"

    def __init__(self, api_key: str, base_url: str, model: str, timeout_s: float) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/ranking"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        self._timeout = timeout_s

    @classmethod
    def from_settings(cls, s: Settings) -> "NvidiaRerankProvider":
        if not s.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required for rerank_provider=nvidia")
        return cls(
            api_key=s.nvidia_api_key,
            base_url=s.nvidia_base_url,
            model=s.nvidia_rerank_model,
            timeout_s=s.openrouter_timeout_s,
        )

    @observe(name="rerank.nvidia")
    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]:
        if not documents:
            return []
        try:
            payload = {
                "model": self.model,
                "query": {"text": query},
                "passages": [{"text": d["text"] or ""} for d in documents],
            }
            resp = requests.post(
                self._url, headers=self._headers, json=payload, timeout=self._timeout
            )
            resp.raise_for_status()
            rankings = resp.json().get("rankings", [])
            # Map index → node_id; rankings come back sorted desc by logit.
            return [
                RankedDoc(
                    node_id=documents[r["index"]]["node_id"],
                    score=float(r.get("logit", r.get("score", 0.0))),
                )
                for r in rankings
                if 0 <= r.get("index", -1) < len(documents)
            ]
        except Exception as exc:
            raise RerankError(f"NVIDIA rerank failed: {exc}") from exc
