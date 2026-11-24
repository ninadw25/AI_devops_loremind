"""
infra/llm/registry.py
─────────────────────
Chat provider registry — maps provider name → factory.

Adding a new provider:
  1. Create infra/llm/<name>.py with a class that satisfies ChatProvider Protocol.
  2. Add one line: _FACTORIES["<name>"] = YourClass.from_settings
  3. Extend the Literal in core/settings.py.

Zero other files need to change.
"""

from __future__ import annotations

from typing import Callable

from core.exceptions import ConfigError
from core.settings import Settings
from infra.llm.base import ChatProvider
from infra.llm.bedrock import BedrockChatProvider
from infra.llm.nvidia import NvidiaChatProvider
from infra.llm.openai import OpenAIChatProvider
from infra.llm.openrouter import OpenRouterChatProvider

_FACTORIES: dict[str, Callable[[Settings], ChatProvider]] = {
    "nvidia":     NvidiaChatProvider.from_settings,
    "openai":     OpenAIChatProvider.from_settings,
    "openrouter": OpenRouterChatProvider.from_settings,
    "bedrock":    BedrockChatProvider.from_settings,
}


def build_chat_provider(settings: Settings) -> ChatProvider:
    """Instantiate the chat provider configured in settings.llm_provider."""
    factory = _FACTORIES.get(settings.llm_provider)
    if factory is None:
        raise ConfigError(
            f"Unknown LLM provider '{settings.llm_provider}'. "
            f"Valid options: {list(_FACTORIES)}"
        )
    return factory(settings)
