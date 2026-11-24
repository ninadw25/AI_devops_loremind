"""
pipeline/extraction/rate_limit.py
─────────────────────────────────
Async throttles for the triple-extraction LLM endpoint.

Two limiters share one duck-typed interface so the extractor stays
provider-agnostic:

    entry = await limiter.acquire(estimated_tokens)   # blocks until under cap
    ... make the LLM call ...
    limiter.reconcile(entry, actual_tokens)           # rewrite reservation

- `AsyncRateLimiter` — requests/min only. Used by NVIDIA / OpenRouter,
  whose binding constraint is RPM. `estimated_tokens` is ignored and
  `reconcile()` is a no-op.
- `TokenAwareRateLimiter` — requests/min AND tokens/min. Used by AWS
  Bedrock, which throttles on both; for large thread prompts the token
  budget binds first. The reservation is made with an estimate, then
  rewritten to the response's actual usage so the window self-corrects.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional


# Rough chars→tokens ratio for prompt estimation (good enough for pacing;
# the reservation is reconciled against real usage right after the call).
_CHARS_PER_TOKEN = 4


def estimate_request_tokens(messages: list[dict], completion_estimate: int) -> int:
    """Estimate total tokens (prompt + expected completion) for a chat call.

    Prompt is approximated from message character length; completion uses
    a caller-supplied estimate (reconciled to actuals afterward). Used only
    to pace a tokens/min budget — not for billing.
    """
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    prompt_est = chars // _CHARS_PER_TOKEN
    return prompt_est + max(0, completion_estimate)


class AsyncRateLimiter:
    """Throttle async calls to at most max_calls within period seconds.

    Uses a sliding window: each acquire() blocks until at least one
    of the last max_calls calls is older than `period` seconds.

    Exposes the token-aware interface (`acquire(estimated_tokens)` /
    `reconcile`) for drop-in compatibility, but ignores token counts.
    """

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int = 0):
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.period
            self._calls = [t for t in self._calls if t > cutoff]
            if len(self._calls) >= self.max_calls:
                wait = self._calls[0] + self.period - now
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    self._calls = [t for t in self._calls
                                   if t > now - self.period]
            self._calls.append(time.monotonic())
        return None

    def reconcile(self, entry, actual_tokens: int) -> None:
        """No-op: RPM-only limiter does not track tokens."""
        return None


class TokenAwareRateLimiter:
    """Sliding-window limiter enforcing BOTH requests/min and tokens/min.

    Each reservation is a mutable ``[timestamp, tokens]`` entry kept in a
    60s (configurable) window. ``acquire(est)`` blocks until adding ``est``
    tokens keeps the request count under ``max_rpm`` AND the token sum under
    ``max_tpm``. ``reconcile(entry, actual)`` rewrites the entry's token
    count once the response's real usage is known, so a conservative or low
    estimate self-corrects for subsequent calls.

    A single request larger than ``max_tpm`` is allowed through only when
    the window is otherwise empty, to avoid a permanent deadlock.
    """

    def __init__(self, max_rpm: int, max_tpm: int, period: float = 60.0):
        self.max_rpm = max_rpm
        self.max_tpm = max_tpm
        self.period = period
        self._entries: list[list[float]] = []  # [timestamp, tokens]
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.period
        self._entries = [e for e in self._entries if e[0] > cutoff]

    async def acquire(self, estimated_tokens: int = 0) -> list[float]:
        est = float(max(0, estimated_tokens))
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)
                req_count = len(self._entries)
                tok_sum = sum(e[1] for e in self._entries)

                rpm_ok = req_count < self.max_rpm
                # Allow an oversized single request through on an empty window.
                tpm_ok = (tok_sum + est) <= self.max_tpm or req_count == 0

                if rpm_ok and tpm_ok:
                    entry = [time.monotonic(), est]
                    self._entries.append(entry)
                    return entry

                # Wait until the oldest in-window entry ages out, then re-check.
                oldest = self._entries[0][0] if self._entries else now
                wait = max(0.05, oldest + self.period - now)
                await asyncio.sleep(min(wait, self.period))

    def reconcile(self, entry: Optional[list[float]], actual_tokens: int) -> None:
        """Rewrite a reservation with the call's real token usage."""
        if entry is not None and actual_tokens and actual_tokens > 0:
            entry[1] = float(actual_tokens)
