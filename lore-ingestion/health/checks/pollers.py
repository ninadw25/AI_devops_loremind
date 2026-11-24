"""Poller liveness probes (heartbeat-based).

The 3-hour poll interval makes the staleness multiplier a deliberate choice: the
agreed 3x rule (fine for the 30s thread watcher) would mean a 9-hour blind spot here.
Scheduled jobs have predictable timing, so we use 1.5x = a 4.5-hour window — a dead
poller surfaces within one missed cycle, not three. Override via
HEALTH_POLLER_STALE_MULTIPLIER. See README.

A poller disabled by configuration (no PAT / no repos) is HEALTHY-with-detail, not a
failure — the system is operating as configured.
"""

import os
from datetime import datetime, timezone

from health.contract import HealthResult, Status
from health.heartbeat import read_heartbeat, uptime_seconds
from health.registry import register

STALE_MULTIPLIER = float(os.getenv("HEALTH_POLLER_STALE_MULTIPLIER", "1.5"))


def _check_poller(name: str, enabled: bool, interval: int) -> HealthResult:
    if not enabled:
        return HealthResult(Status.HEALTHY, "polling disabled by config (not a failure)")

    beat = read_heartbeat(name)
    if beat is None:
        up = uptime_seconds()
        if up < interval:
            return HealthResult(
                Status.DEGRADED, f"no cycle yet (uptime {int(up)}s < interval {interval}s) — starting up"
            )
        return HealthResult(
            Status.DOWN, f"no cycle within first interval ({interval}s) — poller may not be running"
        )

    try:
        last = datetime.fromisoformat(beat)
    except ValueError:
        return HealthResult(Status.DEGRADED, f"unparseable heartbeat value: {beat!r}")

    age = (datetime.now(timezone.utc) - last).total_seconds()
    threshold = interval * STALE_MULTIPLIER
    if age > threshold:
        return HealthResult(
            Status.DEGRADED,
            f"last cycle {int(age)}s ago (>{int(threshold)}s = {STALE_MULTIPLIER}x interval) — stalled or overrunning",
        )
    return HealthResult(Status.HEALTHY, f"last cycle {int(age)}s ago")


@register("gitlab_poller", timeout=2.0)
def check_gitlab_poller() -> HealthResult:
    from common.config import get_settings

    s = get_settings()
    return _check_poller("gitlab_poller", bool(s.gitlab_pat), s.gitlab_poll_interval_seconds)


@register("github_poller", timeout=2.0)
def check_github_poller() -> HealthResult:
    from common.config import get_settings

    s = get_settings()
    enabled = bool(s.github_pat) and bool(os.environ.get("GITHUB_REPOS", "").strip())
    interval = getattr(s, "github_poll_interval_seconds", s.gitlab_poll_interval_seconds)
    return _check_poller("github_poller", enabled, interval)
