"""
chronograph/polling_checkpoint.py
──────────────────────────────────
Generic per-project checkpoint store. Replaces the GitLab-specific
`gitlab_polling_checkpoint.py` with a source-agnostic implementation.

Schema (backwards compatible with v1 gitlab checkpoint):
{
  "version": 2,
  "projects": {
    "gitlab::org/repo": "2026-05-01T00:00:00+00:00",
    "github::owner/repo": "2026-05-01T00:00:00+00:00"
  }
}

Migration: on first read of a v1 file (single `last_synced_at`), the timestamp
is imported into the `projects` key for "gitlab::*" and the file is upgraded.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("chronograph.polling_checkpoint")

SCHEMA_VERSION = 2
_LEGACY_GITLAB_KEY = "gitlab_legacy"


def _checkpoint_path() -> Path:
    state_dir = Path(os.environ.get("LORE_STATE_DIR", "output"))
    return state_dir / "checkpoints" / "chronograph_checkpoint.json"


def _legacy_path() -> Path:
    state_dir = Path(os.environ.get("LORE_STATE_DIR", "output"))
    return state_dir / "checkpoints" / "chronograph_gitlab_checkpoint.json"


def _key(source: str, project_id: str) -> str:
    return f"{source}::{project_id}"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _load_raw() -> dict:
    """Load checkpoint file, handling v1→v2 migration."""
    path = _checkpoint_path()

    if not path.exists():
        # Check for legacy v1 GitLab-only file
        legacy = _legacy_path()
        if legacy.exists():
            try:
                old = json.loads(legacy.read_text(encoding="utf-8"))
                ts = old.get("last_synced_at")
                if ts and old.get("version") == 1:
                    log.info("[checkpoint] Migrating v1 GitLab checkpoint → v2 per-project format")
                    data: dict = {"version": SCHEMA_VERSION, "projects": {}}
                    # Store under a wildcard key — will match any gitlab project
                    data["projects"][_LEGACY_GITLAB_KEY] = ts
                    _atomic_write(path, data)
                    return data
            except Exception as exc:
                log.warning("[checkpoint] Could not migrate legacy file: %s", exc)
        return {"version": SCHEMA_VERSION, "projects": {}}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") not in (1, 2):
            log.warning("[checkpoint] Unknown version %s — treating as first run", data.get("version"))
            return {"version": SCHEMA_VERSION, "projects": {}}
        # Upgrade v1 in-place
        if data.get("version") == 1:
            ts = data.get("last_synced_at")
            data = {"version": SCHEMA_VERSION, "projects": {_LEGACY_GITLAB_KEY: ts} if ts else {}}
            _atomic_write(path, data)
        return data
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("[checkpoint] Corrupt checkpoint (%s) — first run", exc)
        return {"version": SCHEMA_VERSION, "projects": {}}


def load(source: str, project_id: str) -> Optional[datetime]:
    """
    Return the last-synced timestamp for a specific (source, project_id) pair,
    or None if this project has never been synced.
    """
    data = _load_raw()
    projects = data.get("projects", {})

    key = _key(source, project_id)
    raw = projects.get(key)

    # Fall back to the migrated legacy key (any gitlab project on first run after upgrade)
    if raw is None and source == "gitlab" and _LEGACY_GITLAB_KEY in projects:
        raw = projects[_LEGACY_GITLAB_KEY]
        log.info(
            "[checkpoint] Using migrated legacy GitLab timestamp for %s: %s",
            project_id, raw,
        )

    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        log.warning("[checkpoint] Invalid timestamp '%s' for %s/%s: %s", raw, source, project_id, exc)
        return None


def save(source: str, project_id: str, ts: datetime) -> None:
    """Persist the sync timestamp for a project. Thread-safe via atomic write."""
    data = _load_raw()
    data["projects"][_key(source, project_id)] = ts.isoformat()
    data["version"] = SCHEMA_VERSION
    try:
        _atomic_write(_checkpoint_path(), data)
        log.info("[checkpoint] Saved %s/%s → %s", source, project_id, ts.isoformat())
    except Exception as exc:
        log.error("[checkpoint] Save failed for %s/%s: %s", source, project_id, exc)
        raise


def clear(source: str, project_id: str) -> None:
    """Remove a project's checkpoint (forces full re-sync on next cycle)."""
    data = _load_raw()
    key = _key(source, project_id)
    if key in data.get("projects", {}):
        del data["projects"][key]
        _atomic_write(_checkpoint_path(), data)
        log.info("[checkpoint] Cleared %s/%s", source, project_id)
