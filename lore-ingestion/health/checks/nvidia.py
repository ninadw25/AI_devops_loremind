"""NVIDIA embeddings endpoint probe — token-free, cached.

The poller's _embed_symbols() step embeds code via NVIDIA's OpenAI-compatible endpoint.
We probe GET {base_url}/models (lists models, auth required, no tokens spent) rather than
generating an embedding. Cached so a frequent collector poll doesn't hammer it.
"""

import os
import time

import requests

from health.contract import HealthResult, Status
from health.registry import register

_cache: tuple[float, HealthResult] | None = None


@register("nvidia_embeddings", timeout=6.0)
def check_nvidia() -> HealthResult:
    global _cache
    ttl = float(os.getenv("HEALTH_LLM_CACHE_TTL_SECONDS", "300"))
    if _cache is not None and (time.monotonic() - _cache[0]) < ttl:
        return _cache[1]
    res = _probe()
    _cache = (time.monotonic(), res)
    return res


def _probe() -> HealthResult:
    from common.config import get_settings

    s = get_settings()
    if not s.nvidia_api_key:
        return HealthResult(Status.DEGRADED, "NVIDIA_API_KEY not set — embedding step is skipped")

    url = f"{s.nvidia_base_url.rstrip('/')}/models"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {s.nvidia_api_key}"}, timeout=5)
    except Exception as e:  # noqa: BLE001
        return HealthResult(Status.DOWN, f"unreachable: {e}")

    if r.status_code in (401, 403):
        return HealthResult(Status.DOWN, f"auth rejected (HTTP {r.status_code})")
    if r.status_code == 200:
        return HealthResult(Status.HEALTHY, "models endpoint reachable, auth ok (no tokens spent)")
    if r.status_code == 429:
        return HealthResult(Status.DEGRADED, "rate limited (HTTP 429)")
    status = Status.DOWN if r.status_code >= 500 else Status.DEGRADED
    return HealthResult(status, f"unexpected HTTP {r.status_code}")
