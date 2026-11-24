"""GitLab API probe — authenticated reachability via /api/v4/version."""

import requests

from health.contract import HealthResult, Status
from health.registry import register


@register("gitlab_api", timeout=5.0)
def check_gitlab_api() -> HealthResult:
    from common.config import get_settings

    s = get_settings()
    if not s.gitlab_pat:
        return HealthResult(Status.DEGRADED, "GITLAB_PAT not set — GitLab polling disabled")

    url = f"{s.gitlab_base_url.rstrip('/')}/api/v4/version"
    try:
        r = requests.get(url, headers={"PRIVATE-TOKEN": s.gitlab_pat}, timeout=4)
    except Exception as e:  # noqa: BLE001
        return HealthResult(Status.DOWN, f"unreachable: {e}")

    if r.status_code in (401, 403):
        return HealthResult(Status.DOWN, f"auth rejected (HTTP {r.status_code})")
    if r.status_code == 200:
        return HealthResult(Status.HEALTHY, "api reachable, auth ok")
    if r.status_code == 429:
        return HealthResult(Status.DEGRADED, "rate limited (HTTP 429)")
    status = Status.DOWN if r.status_code >= 500 else Status.DEGRADED
    return HealthResult(status, f"unexpected HTTP {r.status_code}")
