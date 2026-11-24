"""The health contract — identical in shape across every Loremind service.

  healthy  — fully functional
  degraded — working but impaired (slow, partial, fallback, stale, disabled, or
             responding-but-suspect data)
  down     — not functional

This service's module is SYNCHRONOUS (psycopg2 / requests / APScheduler),
matching the rest of lore-ingestion. No asyncio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class Status(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"

    @property
    def rank(self) -> int:
        return {"healthy": 0, "degraded": 1, "down": 2}[self.value]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class HealthResult:
    status: Status
    detail: str
    last_check_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "last_check_at": self.last_check_at,
        }


def overall_status(results: Iterable[HealthResult]) -> Status:
    """down if any check is down, else degraded if any is degraded, else healthy."""
    worst = Status.HEALTHY
    for r in results:
        if r.status.rank > worst.rank:
            worst = r.status
    return worst
