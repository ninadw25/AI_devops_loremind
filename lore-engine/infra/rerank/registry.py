"""
infra/rerank/registry.py
─────────────────────────
Rerank provider registry — maps provider name → factory.

Adding a new provider:
  1. Create infra/rerank/<name>.py satisfying RerankProvider Protocol.
  2. Add one line: _FACTORIES["<name>"] = YourClass.from_settings
  3. Extend the Literal in core/settings.py (rerank_provider).

Mirrors infra/llm/registry.py and infra/embeddings/registry.py.
"""

from __future__ import annotations

from typing import Callable

from core.exceptions import ConfigError
from core.settings import Settings
from infra.rerank.base import RerankProvider
from infra.rerank.flashrank import FlashRankProvider
from infra.rerank.nvidia import NvidiaRerankProvider

_FACTORIES: dict[str, Callable[[Settings], RerankProvider]] = {
    "flashrank": FlashRankProvider.from_settings,
    "nvidia":    NvidiaRerankProvider.from_settings,
}


def build_rerank_provider(settings: Settings) -> RerankProvider:
    """Instantiate the reranker configured in settings.rerank_provider."""
    factory = _FACTORIES.get(settings.rerank_provider)
    if factory is None:
        raise ConfigError(
            f"Unknown rerank provider '{settings.rerank_provider}'. "
            f"Valid options: {list(_FACTORIES)}"
        )
    return factory(settings)
