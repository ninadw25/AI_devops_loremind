"""
infra/embeddings/
─────────────────
Embedding provider registry and implementations.

Public API (import from here):
    EmbeddingProvider        — Protocol every implementation satisfies
    build_embedding_provider — factory that picks the right impl from settings
"""

from infra.embeddings.base import EmbeddingProvider
from infra.embeddings.registry import build_embedding_provider

__all__ = ["EmbeddingProvider", "build_embedding_provider"]
