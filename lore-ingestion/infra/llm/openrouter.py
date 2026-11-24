"""
infra/llm/openrouter.py
───────────────────────
OpenRouter chat provider (OpenAI-compatible endpoint).

Used today only by `builders/post_processor.py:label_clusters_llm` for
generating canonical names for HDBSCAN topic clusters. That step is
optional — when `openrouter_api_key` is empty the call site falls
back to using cluster centroids as labels.

`default_model` is `settings.openrouter_llm_model` (default
`google/gemini-2.0-flash-001`).
"""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from common.config import get_settings


class OpenrouterChatProvider:
    """Chat provider wired to OpenRouter (`openrouter.ai/api/v1`)."""

    name = "openrouter"

    def __init__(self) -> None:
        s = get_settings()
        self._base_url = s.openrouter_base_url
        self._api_key = s.openrouter_api_key
        self.default_model = s.openrouter_llm_model

    def make_sync_client(self, **kwargs) -> OpenAI:
        return OpenAI(base_url=self._base_url, api_key=self._api_key, **kwargs)

    def make_async_client(self, **kwargs) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self._base_url, api_key=self._api_key, **kwargs,
        )
