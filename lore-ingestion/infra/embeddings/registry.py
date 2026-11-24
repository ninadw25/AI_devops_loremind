"""
infra/embeddings/registry.py
────────────────────────────
Single entry point for resolving an EmbeddingProvider by name.

    from infra.embeddings import get_embedding_provider

    provider = get_embedding_provider()        # → settings.embedding_provider
    provider = get_embedding_provider("nvidia")

OpenAI and OpenRouter slots are reserved for future implementations
(Settings already permits them as values) — adding them is one new
module under `infra/embeddings/` and one entry in `_PROVIDERS`.
"""

from __future__ import annotations

from common.config import get_settings

from infra.embeddings.base import EmbeddingProvider
from infra.embeddings.bedrock import BedrockEmbeddingProvider
from infra.embeddings.nvidia import NvidiaEmbeddingProvider
from infra.embeddings.openai_provider import OpenAIEmbeddingProvider


_PROVIDERS: dict[str, type[EmbeddingProvider]] = {
    "nvidia":  NvidiaEmbeddingProvider,
    "openai":  OpenAIEmbeddingProvider,
    "bedrock": BedrockEmbeddingProvider,
}


def get_embedding_provider(name: str | None = None) -> EmbeddingProvider:
    """Return a configured EmbeddingProvider.

    If `name` is None, falls back to `settings.embedding_provider`.
    Raises `ValueError` for unknown / not-yet-implemented names so the
    settings value can't silently miss.
    """
    if name is None:
        name = get_settings().embedding_provider
    try:
        cls = _PROVIDERS[name]
    except KeyError as e:
        valid = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Embedding provider {name!r} is not implemented. "
            f"Implemented: {valid}. (Settings allows nvidia/openai/openrouter "
            f"but only the listed providers have backends here.)"
        ) from e
    return cls()
