"""
common/stage_gate.py
────────────────────
Minimum-output sanity gates at every pipeline stage boundary.

Why this exists:
    If a stage produces 0 output rows because of an upstream bug, the
    next stage will happily run on the empty artifact and still log
    `STAGE_SUMMARY ... ok`. Six stages later the operator notices an
    empty Neo4j and has to bisect six runs. A loud failure at the
    boundary cuts that to one.

Usage at the bottom of every `run_*()` or `main()`:

    from common.stage_gate import check_nonempty
    check_nonempty("filter", count=kept, what="events kept",
                   allow_empty=allow_empty)

Every CLI runner should also accept `--allow-empty` and pass it through.
This bypasses the gate intentionally for dry-runs / single-thread
debugging / cases where you genuinely expect 0 output.
"""

from __future__ import annotations

from common.logger import get_logger

_log = get_logger("stage")


class StageOutputEmpty(RuntimeError):
    """Raised when a pipeline stage produced zero output rows.

    Almost always means an upstream stage broke or no input matched the
    expected pattern. Pass `--allow-empty` to bypass when intentional.
    """


class CheckpointCorrupt(RuntimeError):
    """Raised when a stage's resume checkpoint exists but cannot be parsed.

    Default behaviour is to abort: a 30-hour LLM run silently restarting
    because hour-25 had a half-written checkpoint is exactly the failure
    mode this codebase needs to avoid. Pass `--force-restart` to opt
    into deleting the broken checkpoint and starting fresh.
    """


def check_nonempty(
    stage: str,
    *,
    count: int,
    what: str = "rows",
    allow_empty: bool = False,
) -> None:
    """Raise `StageOutputEmpty` if `count == 0` unless `allow_empty` is set.

    The message is identical regardless of which stage called: callers
    pass `stage` and `what` so operators see "Stage 'validator' produced
    0 valid triples." rather than a generic line.
    """
    if count > 0:
        return
    if allow_empty:
        _log.warning(
            "Stage %r produced 0 %s — continuing because --allow-empty set",
            stage, what,
        )
        return
    raise StageOutputEmpty(
        f"Stage {stage!r} produced 0 {what}. "
        f"This is almost always an upstream bug; pass --allow-empty "
        f"to override if you intended an empty run."
    )


class UnscopedThreads(RuntimeError):
    """Raised when threads from an ACL-bearing source reach the builder
    without any scope information.

    Slack/Jira (and every future ACL-bearing source) stamp `scope_id` /
    `scope_ids` in the normalizer and the value is hoisted thread-level
    through grouping → extraction → validation. A thread arriving at the
    builder with `source_ref` set but no scope means a misconfigured
    capability flag (common/sources.py) or a normalizer bug — its nodes
    would be written with an empty scope_ids array and be invisible to
    EVERY user (fail-closed), which looks like data loss at query time.
    Pass `--allow-unscoped` to downgrade to a warning when intentional.
    """


def check_scoped(
    stage: str,
    *,
    unscoped: list,
    total: int,
    allow_unscoped: bool = False,
) -> None:
    """Raise `UnscopedThreads` if any ACL-bearing thread lacks scope info.

    `unscoped` is the list of offending thread ids (used in the message,
    truncated to the first 5). No-op when the list is empty.
    """
    if not unscoped:
        return
    if allow_unscoped:
        _log.warning(
            "Stage %r: %d/%d thread(s) from ACL-bearing sources have no "
            "scope info — their nodes get an empty scope_ids array and stay "
            "invisible to scoped queries (fail-closed). Continuing because "
            "--allow-unscoped set. First few: %s",
            stage, len(unscoped), total, unscoped[:5],
        )
        return
    raise UnscopedThreads(
        f"Stage {stage!r}: {len(unscoped)}/{total} thread(s) from "
        f"ACL-bearing sources (source_ref set) have no scope_id/scope_ids. "
        f"Their nodes would be invisible to every user. This is almost "
        f"always a source capability/normalizer bug — check "
        f"common/sources.py SOURCE_CAPS and the source normalizer. "
        f"Pass --allow-unscoped to override intentionally. "
        f"First few: {unscoped[:5]}"
    )
