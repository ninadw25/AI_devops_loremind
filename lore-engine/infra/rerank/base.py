"""
infra/rerank/base.py
────────────────────
RerankProvider Protocol — the contract every cross-encoder reranker satisfies.

Mirrors infra/llm/base.py and infra/embeddings/base.py: a small interface plus
a from_settings classmethod, selected by a registry. The query pipeline's
ranking stage (domain/retrieval/rank.py) depends only on this Protocol, so
swapping FlashRank (local ONNX) for the NVIDIA reranker NIM is one env var.

rerank() takes the query and a list of RerankDoc {node_id, text} and returns
RankedDoc {node_id, score} sorted descending by relevance. Scores are
provider-specific magnitudes; callers normalize before fusing. The returned
list preserves every input node_id (no silent drops), so downstream fusion
can always join back by id.
"""

from __future__ import annotations

# The Protocol itself lives in core/types.py (so domain/ can depend on it
# without importing infra/). Re-exported here for infra-internal imports
# (registry + providers) and discoverability.
from core.types import RankedDoc, RerankDoc, RerankProvider

__all__ = ["RerankProvider", "RerankDoc", "RankedDoc"]
