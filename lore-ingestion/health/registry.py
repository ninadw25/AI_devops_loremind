"""Check registry + collector (synchronous).

Checks self-register on import. run_all() runs every check in parallel on a thread
pool, each bounded by its own timeout and wrapped in an outer guard so a misbehaving
check is recorded down — never raised out of /health/deep.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Callable

from health.contract import HealthResult, Status, overall_status

log = logging.getLogger("health.registry")

CheckFn = Callable[[], HealthResult]
DEFAULT_TIMEOUT = float(os.getenv("HEALTH_PROBE_TIMEOUT_SECONDS", "3"))


@dataclass
class Check:
    name: str
    fn: CheckFn
    timeout: float


_registry: dict[str, Check] = {}
# Reused across requests; daemon threads so a wedged check never blocks process exit.
_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")


def register(name: str, *, timeout: float | None = None):
    def deco(fn: CheckFn) -> CheckFn:
        _registry[name] = Check(name, fn, timeout if timeout is not None else DEFAULT_TIMEOUT)
        return fn

    return deco


def _guard(check: Check) -> HealthResult:
    try:
        return check.fn()
    except Exception as e:  # noqa: BLE001 — backstop; checks shouldn't raise
        log.exception("health check %r raised", check.name)
        return HealthResult(Status.DOWN, f"check raised: {e}")


def run_all() -> tuple[Status, dict[str, HealthResult]]:
    checks = list(_registry.values())
    futures = [(c, _executor.submit(_guard, c)) for c in checks]
    results: dict[str, HealthResult] = {}
    for c, fut in futures:
        try:
            results[c.name] = fut.result(timeout=c.timeout)
        except FuturesTimeout:
            results[c.name] = HealthResult(Status.DOWN, f"timed out after {c.timeout}s")
        except Exception as e:  # noqa: BLE001
            results[c.name] = HealthResult(Status.DOWN, f"check raised: {e}")
    return overall_status(results.values()), results


def registered_names() -> list[str]:
    return list(_registry.keys())
