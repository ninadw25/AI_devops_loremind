"""GitHub API probe — authenticated reachability via /rate_limit (does not consume quota)."""

import requests

from health.contract import HealthResult, Status
from health.registry import register


@register("github_api", timeout=5.0)
def check_github_api() -> HealthResult:
    from common.config import get_settings

    s = get_settings()
    if not s.github_pat:
        return HealthResult(Status.DEGRADED, "GITHUB_PAT not set — GitHub polling disabled")

    try:
        r = requests.get(
            "https://api.github.com/rate_limit",
            headers={"Authorization": f"token {s.github_pat}", "Accept": "application/vnd.github+json"},
            timeout=4,
        )
    except Exception as e:  # noqa: BLE001
        return HealthResult(Status.DOWN, f"unreachable: {e}")

    if r.status_code in (401, 403):
        return HealthResult(Status.DOWN, f"auth rejected (HTTP {r.status_code})")
    if r.status_code == 200:
        return HealthResult(Status.HEALTHY, "api reachable, auth ok (rate_limit, no quota used)")
    status = Status.DOWN if r.status_code >= 500 else Status.DEGRADED
    return HealthResult(status, f"unexpected HTTP {r.status_code}")
