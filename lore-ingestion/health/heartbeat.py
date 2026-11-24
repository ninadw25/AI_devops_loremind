"""Heartbeat API — the only part of the health module other code calls into.

The poller jobs call heartbeat("gitlab_poller") / heartbeat("github_poller") at the
START of each cycle. The matching checks read it: fresh = healthy, stale = degraded,
missing = down (or "starting up" early in the process lifetime).

Backed by a process-local, thread-safe map. This is correct here because the
APScheduler poller jobs and the health HTTP shim run in the SAME process (the jobs are
BackgroundScheduler threads). If a poller is ever split into its own process, back this
with a shared store (Redis / a Postgres table).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()
_beats: dict[str, str] = {}

# Proxy for process start — set when this module is first imported (at poller startup).
START_TIME = time.monotonic()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def heartbeat(name: str) -> None:
    """Record that a background task is alive. Call at the START of each cycle."""
    with _lock:
        _beats[name] = _now_iso()


def read_heartbeat(name: str) -> str | None:
    with _lock:
        return _beats.get(name)


def uptime_seconds() -> float:
    return time.monotonic() - START_TIME
