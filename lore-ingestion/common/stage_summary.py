"""
common/stage_summary.py
───────────────────────
One-line structured summary at the end of every pipeline stage.

Each runner's `main()` calls `log_summary(stage="...", ...)` exactly
once, just before it returns successfully. The output is greppable
(`grep STAGE_SUMMARY <log>`) and machine-parseable.

Format:
    STAGE_SUMMARY stage=<name> key=value key=value ...

Values with spaces are quoted with double quotes. `None` values are
omitted so callers can pass optional fields uniformly.

Why a separate logger name (`stage`)?
    It keeps the SUMMARY lines easy to filter independently of the
    per-stage chatter. Operators can do
        grep '\\[stage\\]' run.log
    to get a one-line-per-stage timeline of a pipeline run.
"""

from __future__ import annotations

from typing import Any

from common.logger import get_logger

_log = get_logger("stage")


def log_summary(stage: str, **fields: Any) -> None:
    """Emit one structured `STAGE_SUMMARY ...` line for this stage.

    Args:
        stage:   Short stage identifier (e.g. "triple_extractor").
        **fields: Arbitrary key/value pairs. None values are skipped.
                  Strings containing spaces are double-quoted.
    """
    parts: list[str] = [f"stage={stage}"]
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        if " " in text or "\t" in text:
            text = '"' + text.replace('"', '\\"') + '"'
        parts.append(f"{key}={text}")
    _log.info("STAGE_SUMMARY " + " ".join(parts))
