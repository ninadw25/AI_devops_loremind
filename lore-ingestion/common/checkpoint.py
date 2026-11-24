"""
common/checkpoint.py
────────────────────
Unified reverse-chronological fetching checkpoint system.

This module provides a source-agnostic checkpoint format for fetching
data from NOW backwards in time, with resume capability.

Checkpoint format (version 1):
    {
      "version": 1,
      "org": "ezobooks",
      "source": "slack",
      "mode": "reverse_chronological",
      "anchor_timestamp": "2026-06-02T18:00:00+00:00",
      "scope_cursors": {
        "C123456": {
          "status": "completed",  # pending | in_progress | completed | failed
          "latest_ts": "1625097600.123456",      # Anchor (NOW)
          "oldest_fetched": "1625000000.000000", # Progress
          "last_cursor": "dXNlcjpVME...",        # API cursor for resume
          "events_fetched": 150
        }
      },
      "last_run_at": "2026-06-02T18:30:00+00:00",
      "total_events_fetched": 15420,
      "max_history_days": 90,
      "status": "running"  # running | completed | failed
    }

Why this exists
───────────────
- Chronological fetch (oldest first) is the default for most APIs
- For immediate knowledge value, we want newest first
- We need to resume if interrupted (container restart, crash, etc.)
- Deduplication happens via event IDs, so overlapping windows are safe

Atomicity
─────────
State files are written atomically (temp file + rename) so a crash
never leaves a half-written checkpoint. On resume, we either get the
old checkpoint or the new one, never garbage.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from common.config import get_settings
from common.logger import get_logger

log = get_logger("checkpoint")

SCHEMA_VERSION = 1

DEFAULT_MAX_HISTORY_DAYS = 90


class CheckpointStatus:
    """Checkpoint status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus:
    """Overall run status values."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _state_dir() -> Path:
    """Root of all checkpoint state."""
    return Path(os.environ.get("LORE_STATE_DIR", "output")) / "checkpoints"


def _checkpoint_path(org: str, source: str) -> Path:
    """Path to checkpoint file for this org/source combination."""
    return _state_dir() / f"{org}_{source}_reverse_chron.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically — guarantees no half-written checkpoint on crash."""
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
        Path(tmp_path).unlink(missing_ok=True)
        raise


def create_checkpoint(
    org: str,
    source: str,
    scope_ids: list[str],
    max_history_days: int = DEFAULT_MAX_HISTORY_DAYS,
    anchor_timestamp: Optional[datetime] = None,
) -> dict:
    """
    Create a fresh reverse-chronological checkpoint.
    
    Args:
        org: Organization identifier
        source: Source type ("slack", "jira", "gmail")
        scope_ids: List of scope IDs (channel IDs, project keys, etc.)
        max_history_days: How far back to fetch
        anchor_timestamp: Starting point (default: now)
        
    Returns:
        Checkpoint dict ready for use
    """
    if anchor_timestamp is None:
        anchor_timestamp = datetime.now(timezone.utc)
    
    scope_cursors = {}
    for scope_id in scope_ids:
        scope_cursors[scope_id] = {
            "status": CheckpointStatus.PENDING,
            "latest_ts": None,      # Will be set when fetch starts
            "oldest_fetched": None, # Will track progress
            "last_cursor": None,    # API cursor for resume
            "events_fetched": 0,
        }
    
    checkpoint = {
        "version": SCHEMA_VERSION,
        "org": org,
        "source": source,
        "mode": "reverse_chronological",
        "anchor_timestamp": anchor_timestamp.isoformat(),
        "scope_cursors": scope_cursors,
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "total_events_fetched": 0,
        "max_history_days": max_history_days,
        "status": RunStatus.RUNNING,
    }
    
    _atomic_write(_checkpoint_path(org, source), checkpoint)
    log.info(f"Created checkpoint for {org}/{source}: {len(scope_ids)} scopes, {max_history_days}d history")
    
    return checkpoint


def load_checkpoint(org: str, source: str, auto_create: bool = False) -> Optional[dict]:
    """
    Load existing checkpoint for this org/source.
    
    Args:
        org: Organization identifier
        source: Source type
        auto_create: If True and no checkpoint exists, return empty skeleton
        
    Returns:
        Checkpoint dict or None if not found
    """
    path = _checkpoint_path(org, source)
    
    if not path.exists():
        if auto_create:
            return {
                "version": SCHEMA_VERSION,
                "org": org,
                "source": source,
                "mode": "reverse_chronological",
                "anchor_timestamp": None,
                "scope_cursors": {},
                "last_run_at": None,
                "total_events_fetched": 0,
                "max_history_days": DEFAULT_MAX_HISTORY_DAYS,
                "status": None,
            }
        return None
    
    data = json.loads(path.read_text(encoding="utf-8"))
    
    if data.get("version") != SCHEMA_VERSION:
        log.warning(f"Checkpoint version mismatch: expected {SCHEMA_VERSION}, got {data.get('version')}")
        # Could implement migration here
        return None
    
    return data


def save_checkpoint(checkpoint: dict) -> None:
    """Save checkpoint atomically.
    
    Args:
        checkpoint: Checkpoint dict (must have 'org' and 'source' keys)
    """
    org = checkpoint.get("org")
    source = checkpoint.get("source")
    
    if not org or not source:
        raise ValueError("Checkpoint must have 'org' and 'source' keys")
    
    checkpoint["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(_checkpoint_path(org, source), checkpoint)


def update_scope_progress(
    checkpoint: dict,
    scope_id: str,
    status: Optional[str] = None,
    oldest_fetched: Optional[float] = None,
    cursor: Optional[str] = None,
    events_fetched: Optional[int] = None,
) -> dict:
    """
    Update progress for a single scope.
    
    Args:
        checkpoint: The checkpoint dict (modified in place)
        scope_id: Scope identifier (channel ID, project key, etc.)
        status: New status for this scope
        oldest_fetched: New oldest timestamp fetched
        cursor: New API cursor
        events_fetched: Number of events fetched in this batch
        
    Returns:
        Updated checkpoint dict
    """
    scopes = checkpoint.setdefault("scope_cursors", {})
    
    if scope_id not in scopes:
        scopes[scope_id] = {
            "status": CheckpointStatus.PENDING,
            "latest_ts": None,
            "oldest_fetched": None,
            "last_cursor": None,
            "events_fetched": 0,
        }
    
    scope = scopes[scope_id]
    
    if status:
        scope["status"] = status
    if oldest_fetched is not None:
        scope["oldest_fetched"] = oldest_fetched
    if cursor is not None:
        scope["last_cursor"] = cursor
    if events_fetched is not None:
        scope["events_fetched"] = scope.get("events_fetched", 0) + events_fetched
        checkpoint["total_events_fetched"] = checkpoint.get("total_events_fetched", 0) + events_fetched
    
    return checkpoint


def get_scope_checkpoint(checkpoint: dict, scope_id: str) -> Optional[dict]:
    """Get checkpoint data for a specific scope."""
    return checkpoint.get("scope_cursors", {}).get(scope_id)


def get_pending_scopes(checkpoint: dict) -> list[str]:
    """Return list of scope IDs that need processing."""
    pending = []
    for scope_id, scope in checkpoint.get("scope_cursors", {}).items():
        if scope.get("status") in (CheckpointStatus.PENDING, CheckpointStatus.IN_PROGRESS, CheckpointStatus.FAILED):
            pending.append(scope_id)
    return pending


def get_completed_scopes(checkpoint: dict) -> list[str]:
    """Return list of completed scope IDs."""
    completed = []
    for scope_id, scope in checkpoint.get("scope_cursors", {}).items():
        if scope.get("status") == CheckpointStatus.COMPLETED:
            completed.append(scope_id)
    return completed


def mark_run_completed(checkpoint: dict) -> dict:
    """Mark the entire run as completed."""
    checkpoint["status"] = RunStatus.COMPLETED
    checkpoint["completed_at"] = datetime.now(timezone.utc).isoformat()
    return checkpoint


def mark_run_failed(checkpoint: dict, error: str) -> dict:
    """Mark the entire run as failed."""
    checkpoint["status"] = RunStatus.FAILED
    checkpoint["error"] = error[:1000]  # Cap error length
    return checkpoint


def delete_checkpoint(org: str, source: str) -> bool:
    """Delete checkpoint file for this org/source. Returns True if file existed."""
    path = _checkpoint_path(org, source)
    if path.exists():
        path.unlink()
        log.info(f"Deleted checkpoint: {path}")
        return True
    return False


def is_stale(checkpoint: dict, hours: int = 24) -> bool:
    """Check if checkpoint is stale (last run > hours ago)."""
    last_run = checkpoint.get("last_run_at")
    if not last_run:
        return True
    
    last_run_dt = datetime.fromisoformat(last_run)
    age = datetime.now(timezone.utc) - last_run_dt
    return age > timedelta(hours=hours)


def report_progress(checkpoint: dict) -> dict:
    """
    Generate progress report from checkpoint.
    
    Returns dict with:
        - total_scopes: Total number of scopes
        - completed: Number of completed scopes
        - in_progress: Number of in-progress scopes
        - pending: Number of pending scopes
        - failed: Number of failed scopes
        - percent: Overall completion percentage
        - total_events: Total events fetched
    """
    scopes = checkpoint.get("scope_cursors", {})
    total = len(scopes)
    
    if total == 0:
        return {
            "total_scopes": 0,
            "completed": 0,
            "in_progress": 0,
            "pending": 0,
            "failed": 0,
            "percent": 100.0,
            "total_events": 0,
            "scopes_detail": [],
        }
    
    completed = sum(1 for s in scopes.values() if s.get("status") == CheckpointStatus.COMPLETED)
    in_progress = sum(1 for s in scopes.values() if s.get("status") == CheckpointStatus.IN_PROGRESS)
    pending = sum(1 for s in scopes.values() if s.get("status") == CheckpointStatus.PENDING)
    failed = sum(1 for s in scopes.values() if s.get("status") == CheckpointStatus.FAILED)
    
    scopes_detail = []
    for scope_id, scope in scopes.items():
        detail = {
            "scope_id": scope_id,
            "status": scope.get("status"),
            "events_fetched": scope.get("events_fetched", 0),
        }
        
        # Calculate time-based progress if we have timestamps
        latest = scope.get("latest_ts")
        oldest = scope.get("oldest_fetched")
        if latest and oldest:
            try:
                latest_val = float(latest) if isinstance(latest, str) else latest
                oldest_val = float(oldest) if isinstance(oldest, str) else oldest
                
                # Calculate percentage of time window covered
                # (assuming linear distribution, which is approximate)
                max_days = checkpoint.get("max_history_days", DEFAULT_MAX_HISTORY_DAYS)
                max_seconds = max_days * 24 * 3600
                current_seconds = latest_val - oldest_val
                detail["time_progress_pct"] = min(100.0, (current_seconds / max_seconds) * 100)
            except (ValueError, TypeError):
                pass
        
        scopes_detail.append(detail)
    
    return {
        "total_scopes": total,
        "completed": completed,
        "in_progress": in_progress,
        "pending": pending,
        "failed": failed,
        "percent": (completed / total) * 100,
        "total_events": checkpoint.get("total_events_fetched", 0),
        "scopes_detail": scopes_detail,
    }


def print_progress_report(checkpoint: dict) -> None:
    """Print human-readable progress report to log."""
    report = report_progress(checkpoint)
    
    log.info("=" * 60)
    log.info(f"Reverse-Chronological Fetch Progress for {checkpoint.get('org')}/{checkpoint.get('source')}")
    log.info(f"  Total scopes: {report['total_scopes']}")
    log.info(f"  Completed:    {report['completed']} ({report['percent']:.1f}%)")
    log.info(f"  In progress:  {report['in_progress']}")
    log.info(f"  Pending:      {report['pending']}")
    log.info(f"  Failed:       {report['failed']}")
    log.info(f"  Total events: {report['total_events']}")
    log.info("=" * 60)
    
    # Detail for in-progress scopes
    for detail in report.get("scopes_detail", []):
        if detail["status"] == CheckpointStatus.IN_PROGRESS:
            progress_str = ""
            if "time_progress_pct" in detail:
                progress_str = f" ({detail['time_progress_pct']:.1f}% time window)"
            log.info(f"  → {detail['scope_id']}: {detail['events_fetched']} events{progress_str}")
