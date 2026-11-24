"""
chronograph/gitlab_polling_checkpoint.py
───────────────────────────────────────
Manages the incremental-sync state for the GitLab polling mechanism.

This is a simplified version of `common/watermark.py`, tailored for the
GitLab poller's need to store a single timestamp representing the
`last_synced_at` point.

The checkpoint is a single JSON file, `chronograph_gitlab_checkpoint.json`,
stored in the standard checkpoint directory. It survives process restarts
and ensures that each polling cycle can continue from where the last
successful one left off.

Schema:
    {
      "version": 1,
      "last_synced_at": "2026-05-28T10:00:00+00:00"
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from common.logger import get_logger

log = get_logger("chronograph.gitlab_polling_checkpoint")

SCHEMA_VERSION = 1


def _get_checkpoint_dir() -> Path:
    """Returns the directory where checkpoints are stored."""
    # Re-uses the same state directory logic as the main pipeline's watermarks.
    return Path(os.environ.get("LORE_STATE_DIR", "output")) / "checkpoints"


def _get_checkpoint_path() -> Path:
    """Returns the full path to the GitLab polling checkpoint file."""
    return _get_checkpoint_dir() / "chronograph_gitlab_checkpoint.json"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically to prevent corruption from partial writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)
    except Exception:
        # Best-effort cleanup if the rename failed
        tmp_path.unlink(missing_ok=True)
        raise


def load_checkpoint() -> str | None:
    """
    Reads the last-synced marker from persistent storage.

    Returns:
        The UTC ISO timestamp of the last successful sync, or None if no
        checkpoint exists yet (first run).
    """
    path = _get_checkpoint_path()
    if not path.exists():
        log.info("GitLab checkpoint not found, will perform a full sync.")
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != SCHEMA_VERSION:
            log.warning(
                "GitLab checkpoint version mismatch (expected %s, got %s). "
                "Ignoring checkpoint and performing a full sync.",
                SCHEMA_VERSION, data.get('version')
            )
            return None
        last_synced_at = data.get("last_synced_at")
        if last_synced_at:
            # Validate that it's a valid ISO 8601 timestamp
            datetime.fromisoformat(last_synced_at)
        return last_synced_at
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        log.warning(
            "Could not parse GitLab checkpoint file at %s. Error: %s. "
            "Ignoring checkpoint and performing a full sync.", path, e
        )
        return None


def save_checkpoint(marker: str):
    """
    Persists the new sync marker after a successful poll cycle.

    Args:
        marker: The UTC ISO timestamp to save as the new checkpoint.
    """
    path = _get_checkpoint_path()
    log.info("Saving new GitLab checkpoint: %s", marker)
    state = {
        "version": SCHEMA_VERSION,
        "last_synced_at": marker,
    }
    try:
        # Validate that the marker is a valid ISO 8601 timestamp before writing
        datetime.fromisoformat(marker)
        _atomic_write(path, state)
    except (ValueError, TypeError) as e:
        log.error("Invalid timestamp format for checkpoint marker: %s. Error: %s", marker, e)
        raise
    except Exception as e:
        log.error("Failed to save GitLab checkpoint to %s. Error: %s", path, e)
        raise
