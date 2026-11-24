"""
pipeline/extraction/checkpoint.py
─────────────────────────────────
Resume-checkpoint helpers for the LLM extraction stage.

`_load_checkpoint` raises `CheckpointCorrupt` by default if the file
exists but cannot be parsed. Pass `force_restart=True` to delete the
broken file and start over (mirrors the `--force-restart` CLI flag).
"""

from __future__ import annotations

import json
from pathlib import Path

from common.logger import get_logger
from common.stage_gate import CheckpointCorrupt

log = get_logger("pipeline.triple_extractor")


def _load_checkpoint(path: Path, *, force_restart: bool = False) -> dict:
    """Load existing checkpoint or return empty dict.

    Raises `CheckpointCorrupt` if the checkpoint file exists but
    cannot be parsed. Pass `force_restart=True` to delete the bad
    file and start over (mirrors `--force-restart` on the CLI).
    """
    if not path.exists():
        return {}
    if force_restart:
        log.info("--force-restart: discarding checkpoint %s", path)
        path.unlink(missing_ok=True)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise CheckpointCorrupt(
            f"Extraction checkpoint at {path} could not be parsed "
            f"({type(e).__name__}: {e}). Re-run with --force-restart "
            f"to discard and start fresh."
        ) from e


def _save_checkpoint(path: Path, results: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
