"""
common/watermark.py
───────────────────
Per-user, per-org incremental-sync state.

Why this exists
───────────────
The pipeline runs on a cron-like schedule (every 6 hours on Fargate).
Each run must continue from EXACTLY where the previous run stopped —
no overlap that re-bills LLM tokens, no gap that loses a message.

Gmail's `after:<epoch>` query operator filters server-side by
`internalDate` (Gmail's monotonic server-receipt timestamp). It is
strictly greater-than, so `after:1716210325` returns messages whose
`internalDate > 1716210325`. If we record `max(internalDate)` of the
last successful run per user, the next run picks up at the very next
message that Gmail received — by construction.

Atomicity rule
──────────────
The watermark advances ONLY after the FULL pipeline (fetch → filter →
extract → validate → embed → Neo4j upsert) succeeds. A mid-pipeline
crash leaves the watermark unchanged; the next run re-fetches the same
window, and downstream stages deduplicate by message-id / node-id so
re-processing is free and lossless.

State file
──────────
`${LORE_STATE_DIR}/checkpoints/{org}_sync.json` — one JSON per org.
LORE_STATE_DIR defaults to `output`. In Docker we bind-mount it; in
Fargate we mount EFS at the same path. Code is identical in both.

Schema (version 1):
    {
      "version": 1,
      "org": "ezobooks",
      "last_run_started_at":   "2026-05-20T01:00:00+00:00",
      "last_run_completed_at": "2026-05-20T01:05:25+00:00",
      "last_run_status":       "ok" | "failed",
      "users": {
        "alice@ezobooks.in": {
          "last_internal_date_ms": 1716210325000,
          "last_internal_date_iso": "2026-05-20T01:05:25+00:00"
        },
        ...
      },
      "stats": {
        "messages_processed_last_run": 132,
        "threads_processed_last_run":  47
      }
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SCHEMA_VERSION = 1


def state_dir() -> Path:
    """Root of all sync state. Override with $LORE_STATE_DIR.

    On-prem Docker:  bind-mount a host dir to /app/output → set LORE_STATE_DIR=/app/output
    AWS Fargate:     mount EFS to /app/output → set LORE_STATE_DIR=/app/output
    Local dev:       leave unset → uses ./output
    """
    return Path(os.environ.get("LORE_STATE_DIR", "output")) / "checkpoints"


def _path(org: str) -> Path:
    return state_dir() / f"{org}_sync.json"


def load(org: str) -> dict:
    """Read the watermark file. Returns an empty skeleton on first run."""
    p = _path(org)
    if not p.exists():
        return {
            "version": SCHEMA_VERSION,
            "org": org,
            "last_run_started_at":   None,
            "last_run_completed_at": None,
            "last_run_status":       None,
            "users": {},
            "stats": {},
        }
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"Watermark schema version mismatch at {p}: "
            f"expected {SCHEMA_VERSION}, got {data.get('version')}. "
            f"Migrate or delete the file (next run will re-fetch from scratch)."
        )
    return data


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically — guarantees no half-written watermark on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup if the rename failed
        Path(tmp_path).unlink(missing_ok=True)
        raise


def mark_run_started(org: str) -> dict:
    """Record run-start timestamp. Returns the full state dict."""
    state = load(org)
    state["last_run_started_at"] = datetime.now(timezone.utc).isoformat()
    state["last_run_status"] = "running"
    _atomic_write(_path(org), state)
    return state


def mark_run_failed(org: str, error: str) -> None:
    """Record a failure WITHOUT advancing per-user watermarks.

    The next run will re-fetch the same window — downstream stages
    deduplicate so this is safe and idempotent.
    """
    state = load(org)
    state["last_run_status"]       = "failed"
    state["last_run_completed_at"] = datetime.now(timezone.utc).isoformat()
    state["last_run_error"] = error[:1000]  # cap stored error length
    _atomic_write(_path(org), state)


def commit_run(
    org: str,
    user_internal_dates_ms: dict[str, int],
    *,
    messages_processed: int,
    threads_processed: int,
) -> None:
    """Advance the watermark — call ONLY after the full pipeline succeeds.

    Args:
        user_internal_dates_ms: {email: max_internalDate_ms_observed_this_run}
            for each user successfully fetched. Watermark for a user only
            advances if the new value is greater than the existing one
            (defensive — protects against accidental backwards moves).
        messages_processed: count for telemetry/reporting
        threads_processed:  count for telemetry/reporting
    """
    state = load(org)
    users = state.setdefault("users", {})

    for email, new_ms in user_internal_dates_ms.items():
        if new_ms is None:
            continue
        existing = users.get(email, {})
        prev_ms = existing.get("last_internal_date_ms", 0) or 0
        if new_ms <= prev_ms:
            # No new mail for this user, or older than what we already saw.
            continue
        users[email] = {
            "last_internal_date_ms":  new_ms,
            "last_internal_date_iso": datetime.fromtimestamp(
                new_ms / 1000, tz=timezone.utc
            ).isoformat(),
        }

    state["last_run_completed_at"] = datetime.now(timezone.utc).isoformat()
    state["last_run_status"] = "ok"
    state.pop("last_run_error", None)
    state["stats"] = {
        "messages_processed_last_run": messages_processed,
        "threads_processed_last_run":  threads_processed,
    }
    _atomic_write(_path(org), state)


def get_user_since_epoch(state: dict, email: str) -> Optional[int]:
    """Return the `after:<seconds>` value to use for this user's Gmail query.

    Gmail's `after:<seconds>` is strict greater-than, so passing the
    last-seen internalDate (in seconds) gives lossless continuation.

    Returns None if this user has never been fetched (first run).
    """
    entry = (state.get("users") or {}).get(email)
    if not entry:
        return None
    ms = entry.get("last_internal_date_ms")
    if not ms:
        return None
    return ms // 1000  # Gmail's `after:` is in seconds, not ms
