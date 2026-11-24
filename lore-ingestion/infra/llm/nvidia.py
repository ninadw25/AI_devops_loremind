"""
infra/llm/nvidia.py
───────────────────
NVIDIA Build chat provider (OpenAI-compatible endpoint).

`default_model` is `settings.extraction_model` because the only chat
caller on NVIDIA today is the triple-extraction stage. If a future
caller needs a different NVIDIA model, override `model=` at the call
site rather than parameterizing the provider.
"""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from common.config import get_settings


class NvidiaChatProvider:
    """Chat provider wired to NVIDIA Build (`integrate.api.nvidia.com`)."""

    name = "nvidia"

    def __init__(self) -> None:
        s = get_settings()
        self._base_url = s.nvidia_base_url
        self._api_key = s.nvidia_api_key
        self.default_model = s.extraction_model

    def make_sync_client(self, **kwargs) -> OpenAI:
        return OpenAI(base_url=self._base_url, api_key=self._api_key, **kwargs)

    def make_async_client(self, **kwargs) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self._base_url, api_key=self._api_key, **kwargs,
        )
