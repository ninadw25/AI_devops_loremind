"""
infra/gmail/throttle.py
───────────────────────
Token-bucket pacing against Gmail's per-mailbox quota (250 units/sec/user,
`messages.get` = 5 units). Replaces the fixed sleep-per-batch: instead of
guessing a pause, spend from a budget that refills at `rate` units/sec and
block only when the budget is dry.

Adaptive (AIMD, same shape as TCP): any 429 halves the pace immediately;
every clean batch creeps it back up toward the target. So the fetcher
self-tunes to whatever ceiling this particular tenant actually has, instead
of hardcoding Google's published number and hoping.

One instance per mailbox (quota buckets are per-user). Thread-safe, because
a mailbox's in-flight batches run on a small worker pool.
"""

from __future__ import annotations

import threading
import time

# Gmail quota: messages.get costs 5 units; the per-user bucket holds ~250.
UNITS_PER_GET = 5
BUCKET_CAPACITY = 250.0


class QuotaThrottle:
    """acquire() before each batch; penalize() on 429; reward() on a clean one."""

    def __init__(
        self,
        rate: float,
        floor: float = 25.0,
        step: float = 5.0,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.target = float(rate)   # cruising pace (units/sec) we recover toward
        self.rate = float(rate)     # current pace, moves on penalize/reward
        self.floor = floor          # never slower than this — progress must continue
        self.step = step            # additive recovery per clean batch
        self._clock, self._sleep = clock, sleep
        self._tokens = BUCKET_CAPACITY  # start full: first batches go immediately
        self._at = clock()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = self._clock()
        self._tokens = min(BUCKET_CAPACITY, self._tokens + (now - self._at) * self.rate)
        self._at = now

    def acquire(self, units: float) -> None:
        """Block until `units` are available, then spend them."""
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= units:
                    self._tokens -= units
                    return
                wait = (units - self._tokens) / self.rate
            # Sleep outside the lock so a penalize() from another worker lands.
            self._sleep(min(wait, 1.0))

    def penalize(self) -> None:
        """A 429 happened: halve the pace (multiplicative decrease)."""
        with self._lock:
            self.rate = max(self.floor, self.rate / 2)

    def reward(self) -> None:
        """A clean batch: creep back toward target (additive increase)."""
        with self._lock:
            self.rate = min(self.target, self.rate + self.step)
