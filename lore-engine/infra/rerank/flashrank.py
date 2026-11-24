"""
infra/rerank/flashrank.py
─────────────────────────
Local cross-encoder reranker backed by FlashRank (ONNX, CPU).

Default reranker for the unstructured query path. Zero per-query cost, no
network call; the model (~ms-marco-MiniLM-L-12-v2, a few hundred MB) is loaded
once at process start (api/main.py lifespan) and reused.

The flashrank import is lazy (inside __init__) so this module imports cleanly
in environments where the optional dependency is absent — the registry raises
a clear ConfigError only when this provider is actually selected.
"""

from __future__ import annotations

from core.exceptions import ConfigError, RerankError
from core.logging import get_logger
from core.observability import observe
from core.settings import Settings
from core.types import RankedDoc, RerankDoc

log = get_logger(__name__)


class FlashRankProvider:
    name = "flashrank"

    def __init__(self, model: str) -> None:
        self.model = model
        try:
            from flashrank import Ranker  # lazy: optional dependency
        except ImportError as exc:
            raise ConfigError(
                "rerank_provider=flashrank requires the 'flashrank' package. "
                "Install with: pip install 'lore-backend[rerank]'"
            ) from exc
        # Loads / caches the ONNX model. Done once per process.
        self._ranker = Ranker(model_name=model)
        # Imported here too so rerank() doesn't re-import per call.
        from flashrank import RerankRequest  # noqa: F401
        self._RerankRequest = RerankRequest
        log.info("flashrank_ready", model=model)

    @classmethod
    def from_settings(cls, s: Settings) -> "FlashRankProvider":
        return cls(model=s.rerank_model)

    @observe(name="rerank.flashrank")
    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]:
        if not documents:
            return []
        try:
            passages = [{"id": d["node_id"], "text": d["text"] or ""} for d in documents]
            req = self._RerankRequest(query=query, passages=passages)
            results = self._ranker.rerank(req)  # sorted desc by score
            return [
                RankedDoc(node_id=str(r["id"]), score=float(r["score"]))
                for r in results
            ]
        except Exception as exc:
            raise RerankError(f"FlashRank rerank failed: {exc}") from exc
