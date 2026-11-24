"""
core/observability.py
─────────────────────
Langfuse client singleton with a safe no-op fallback.

This module replaces both engine/tracing.py (which was the live path)
and common/observability.py (which was dead code with its own separate
lazy singleton).

Exports:
    lf                  — Langfuse client (real or no-op)
    observe             — @observe decorator (real or no-op pass-through)
    propagate_attributes — context manager for trace metadata

Usage:
    from core.observability import lf, observe, propagate_attributes

    @observe(name="embed-query")
    def embed_query(...): ...

    with propagate_attributes(user_id="alice", session_id="ezobooks"):
        result = embed_query(...)

    lf.update_current_span(input={"q": q}, output={"ok": True})
    lf.score_current_trace(name="confidence", value=0.9, data_type="NUMERIC")

If LANGFUSE_DISABLED=true or langfuse is not installed or initialisation
fails, all calls silently no-op and the application continues normally.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Callable

log = logging.getLogger("core.observability")

# ── No-op implementations ────────────────────────────────────────────


class _NoopClient:
    """Drop-in replacement for the Langfuse client when tracing is off."""

    def update_current_span(self, **kw: Any) -> None:
        pass

    def update_current_trace(self, **kw: Any) -> None:
        pass

    def score_current_trace(self, **kw: Any) -> None:
        pass

    def flush(self) -> None:
        pass


def _noop_observe(
    name: str | None = None,
    as_type: str | None = None,
    **kw: Any,
) -> Callable:
    """No-op @observe decorator — returns the function unchanged."""
    def decorator(fn: Callable) -> Callable:
        return fn
    return decorator


@contextmanager
def _noop_propagate(**kw: Any):
    yield


# ── Initialisation ───────────────────────────────────────────────────

def _init():
    """Try to initialise Langfuse. Return (lf, observe, propagate_attributes)."""
    try:
        from langfuse import get_client, observe as _observe, propagate_attributes as _propagate
        client = get_client()
        log.info("Langfuse tracing enabled")
        return client, _observe, _propagate
    except ImportError:
        log.warning("langfuse package not installed; tracing disabled")
    except Exception as exc:
        log.warning("Langfuse init failed (%s); tracing disabled", exc)

    return _NoopClient(), _noop_observe, _noop_propagate


lf, observe, propagate_attributes = _init()
