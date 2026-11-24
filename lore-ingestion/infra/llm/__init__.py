"""
infra.llm
─────────
LLM chat-completions providers.

The public surface is `ChatProvider` (the Protocol) and
`get_chat_provider(name=None)` (the registry resolver). Provider
implementations live in sibling modules.
"""

from infra.llm.base import ChatProvider
from infra.llm.registry import get_chat_provider

__all__ = ["ChatProvider", "get_chat_provider"]
