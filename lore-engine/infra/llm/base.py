"""
infra/llm/base.py
─────────────────
ChatProvider Protocol — the contract every LLM implementation must satisfy.

Stream format:
    chat_stream yields (kind, text) pairs where kind is:
      "reasoning"  — model's internal chain-of-thought (discard or log)
      "content"    — the actual answer tokens to send to the user

    Providers that don't support reasoning only yield ("content", text).
    synthesis.py and callers filter for kind == "content".
"""

from __future__ import annotations

# The ChatProvider Protocol lives in core/types.py so domain/ can depend on it
# without importing infra/ (per the layer rule in CLAUDE.md). Re-exported here
# for infra-internal imports (registry, providers) and discoverability.
from core.types import ChatProvider

__all__ = ["ChatProvider"]
