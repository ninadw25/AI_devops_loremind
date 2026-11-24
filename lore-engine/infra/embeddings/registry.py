"""
infra/embeddings/registry.py
─────────────────────────────
Embedding provider registry — maps provider name → factory.

Adding a new provider:
  1. Create infra/embeddings/<name>.py satisfying EmbeddingProvider Protocol.
  2. Add one line: _FACTORIES["<name>"] = YourClass.from_settings
  3. Extend the Literal in core/settings.py.
"""

from __future__ import annotations

from typing import Callable

from core.exceptions import ConfigError
from core.settings import Settings
from infra.embeddings.base import EmbeddingProvider
from infra.embeddings.bedrock import BedrockEmbeddingProvider
from infra.embeddings.nvidia import NvidiaEmbeddingProvider
from infra.embeddings.openai import OpenAIEmbeddingProvider
from infra.embeddings.openrouter import OpenRouterEmbeddingProvider

_FACTORIES: dict[str, Callable[[Settings], EmbeddingProvider]] = {
    "nvidia":     NvidiaEmbeddingProvider.from_settings,
    "openai":     OpenAIEmbeddingProvider.from_settings,
    "openrouter": OpenRouterEmbeddingProvider.from_settings,
    "bedrock":    BedrockEmbeddingProvider.from_settings,
}


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Instantiate the embedding provider configured in settings.embedding_provider."""
    factory = _FACTORIES.get(settings.embedding_provider)
    if factory is None:
        raise ConfigError(
            f"Unknown embedding provider '{settings.embedding_provider}'. "
            f"Valid options: {list(_FACTORIES)}"
        )
    return factory(settings)
