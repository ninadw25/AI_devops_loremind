"""
infra/llm/
──────────
Chat provider registry and implementations.

Public API (import from here):
    ChatProvider        — Protocol every implementation satisfies
    build_chat_provider — factory that picks the right impl from settings
"""

from infra.llm.base import ChatProvider
from infra.llm.registry import build_chat_provider

__all__ = ["ChatProvider", "build_chat_provider"]
