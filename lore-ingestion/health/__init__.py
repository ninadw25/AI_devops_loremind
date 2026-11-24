"""Self-contained health module for lore-ingestion.

Exposes /health (shallow) + /health/deep (functional probes) via a tiny zero-dependency
stdlib HTTP shim started by the poller runner. The only symbol other code imports from
here is `heartbeat`.
"""

from health.heartbeat import heartbeat

__all__ = ["heartbeat"]
