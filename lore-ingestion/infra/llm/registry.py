"""
infra/llm/registry.py
─────────────────────
Single entry point for resolving a ChatProvider by name.

Call sites do:

    from infra.llm import get_chat_provider

    provider = get_chat_provider()              # → settings.llm_provider
    provider = get_chat_provider("openrouter")  # → explicit pin

The registry is module-level state seeded at import time, not a
runtime hook. Adding a new provider is two lines: one import, one
entry in `_PROVIDERS`.
"""

from __future__ import annotations

from common.config import get_settings

from infra.llm.base import ChatProvider
from infra.llm.bedrock import BedrockChatProvider
from infra.llm.nvidia import NvidiaChatProvider
from infra.llm.openrouter import OpenrouterChatProvider


_PROVIDERS: dict[str, type[ChatProvider]] = {
    "nvidia": NvidiaChatProvider,
    "openrouter": OpenrouterChatProvider,
    "bedrock": BedrockChatProvider,
}


def get_chat_provider(name: str | None = None) -> ChatProvider:
    """Return a configured ChatProvider.

    If `name` is None, falls back to `settings.llm_provider`.
    Raises `ValueError` for unknown names so a typo doesn't silently
    pick the default.
    """
    if name is None:
        name = get_settings().llm_provider
    try:
        cls = _PROVIDERS[name]
    except KeyError as e:
        valid = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown LLM provider {name!r}. "
            f"Valid options: {valid}."
        ) from e
    return cls()
