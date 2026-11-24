"""Active chronograph store probe (PostgreSQL + pgvector).

Reuses the existing make_store() factory, so it probes the exact store the pollers
use — a real connect plus a cheap query, not just a TCP check.
"""

from health.contract import HealthResult, Status
from health.registry import register


@register("store", timeout=8.0)
def check_store() -> HealthResult:
    from chronograph.store import make_store
    from common.config import get_settings

    settings = get_settings()
    backend = "postgres"
    store = make_store(settings)

    try:
        ok = store.connect()
    except Exception as e:  # noqa: BLE001
        return HealthResult(Status.DOWN, f"{backend} store connect raised: {e}")
    if not ok:
        return HealthResult(Status.DOWN, f"cannot connect to {backend} store")

    try:
        # Cheap real read against a non-existent project — exercises the query path,
        # should return 0. Verifies "responding correctly", not just "connected".
        count = store.get_commit_count("__healthcheck_probe__")
        if not isinstance(count, int):
            return HealthResult(
                Status.DEGRADED,
                f"{backend} store connected but probe query returned {type(count).__name__}",
            )
        return HealthResult(Status.HEALTHY, f"{backend} store reachable (probe query ok)")
    except Exception as e:  # noqa: BLE001
        return HealthResult(Status.DEGRADED, f"{backend} store connected but probe query failed: {e}")
    finally:
        try:
            store.disconnect()
        except Exception:  # noqa: BLE001
            pass
