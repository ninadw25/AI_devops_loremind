"""
infra/llm/base.py
─────────────────
ChatProvider Protocol — the thin abstraction every LLM provider must
expose. Call sites construct the appropriate client (sync or async)
from the provider and use it directly, so provider-specific concerns
like timeouts, JSON-mode quirks, and retry policy stay at the call
site where they belong.

Each provider also advertises a `default_model`. Call sites are free
to override it (e.g., for evaluation runs against different models),
but in production the default keeps the LLM choice out of business
logic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from openai import AsyncOpenAI, OpenAI


@runtime_checkable
class ChatProvider(Protocol):
    """A configured OpenAI-compatible chat-completions endpoint.

    Implementations bake (base_url, api_key) in at construction and
    expose factory methods so callers can pass through `timeout`,
    `max_retries`, etc. without leaking provider URLs into call sites.
    """

    #: Short identifier used in logs and the registry ("nvidia", "openrouter").
    name: str

    #: Model the provider considers canonical for this codebase.
    default_model: str

    def make_sync_client(self, **kwargs) -> OpenAI:
        """Return a configured sync `openai.OpenAI` client."""
        ...

    def make_async_client(self, **kwargs) -> AsyncOpenAI:
        """Return a configured async `openai.AsyncOpenAI` client."""
        ...
