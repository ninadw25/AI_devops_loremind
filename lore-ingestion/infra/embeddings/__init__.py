"""
infra.embeddings
────────────────
Text embedding providers.

The public surface is `EmbeddingProvider` (the Protocol) and
`get_embedding_provider(name=None)` (the registry resolver).
"""

from infra.embeddings.base import EmbeddingProvider
from infra.embeddings.registry import get_embedding_provider

__all__ = ["EmbeddingProvider", "get_embedding_provider"]
