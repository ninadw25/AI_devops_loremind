"""
common/reconcile.py
───────────────────
Source-agnostic snapshot-diff for detecting EDITS and DELETES without
re-scanning full history.

The problem
───────────
On a standard (non-Grid) workspace, Slack's `conversations.history`:
  • returns the LATEST text for an edited message (+ an `edited:{user,ts}` mark)
  • simply OMITS a deleted message — there is no tombstone

So the only way to learn that something was edited or deleted is to compare a
fresh fetch against what we saw last time. To avoid overloading the source we
NEVER diff all of history — only a bounded, recent reconcile WINDOW (e.g. the
trailing 14–30 days), because edits/deletes overwhelmingly happen to recent
messages. Older messages age out of the window and are treated as immutable.

This module is the pure diff core: given the previous snapshot and the current
one (both `source_id -> fingerprint`), it reports what changed. It does no IO
and knows nothing about Slack specifically — a Slack/Jira/Gmail runner builds
the fingerprints from its own fetched events and persists the snapshot.

A "fingerprint" is the minimal content identity of a message: a hash of its
body plus its edit timestamp. If either changes, the message was edited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from common.hashing import short_hash


@dataclass(frozen=True)
class Fingerprint:
    """Minimal content identity of one message, for change detection."""
    source_id: str          # stable key, e.g. "C123:1780151456.181247"
    body_hash: str          # short_hash of the normalized body text
    edited_ts: str = ""     # Slack `edited.ts` (or "") — bumps on every edit
    timestamp: str = ""     # original post time (ISO) — for window bookkeeping

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "body_hash": self.body_hash,
            "edited_ts": self.edited_ts,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        return cls(
            source_id=d["source_id"],
            body_hash=d.get("body_hash", ""),
            edited_ts=d.get("edited_ts", ""),
            timestamp=d.get("timestamp", ""),
        )

    def changed_from(self, other: "Fingerprint") -> bool:
        """True if this looks edited relative to `other`."""
        return self.body_hash != other.body_hash or self.edited_ts != other.edited_ts


def fingerprint_event(event: dict) -> Fingerprint:
    """Build a Fingerprint from a normalized KnowledgeEvent dict.

    Works on the JSONL records the connectors write (top-level `source_id`,
    `body`, `timestamp`, and the Slack `raw.edited.ts` when present).
    """
    raw = event.get("raw") or {}
    edited = raw.get("edited") or {}
    body = event.get("body") or ""
    return Fingerprint(
        source_id=event.get("source_id") or "",
        body_hash=short_hash(body) if body else "",
        edited_ts=str(edited.get("ts") or ""),
        timestamp=event.get("timestamp") or "",
    )


def build_snapshot(events: Iterable[dict]) -> dict[str, dict]:
    """Index an iterable of event dicts into a `source_id -> fingerprint` map."""
    snap: dict[str, dict] = {}
    for ev in events:
        fp = fingerprint_event(ev)
        if fp.source_id:
            snap[fp.source_id] = fp.to_dict()
    return snap


@dataclass
class ReconcileDiff:
    """The result of diffing a previous snapshot against the current one."""
    edited: list[str] = field(default_factory=list)    # source_ids that changed
    deleted: list[str] = field(default_factory=list)   # were present, now gone
    added: list[str] = field(default_factory=list)     # newly seen (informational)
    unchanged: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.edited or self.deleted)

    def summary(self) -> dict:
        return {
            "edited": len(self.edited),
            "deleted": len(self.deleted),
            "added": len(self.added),
            "unchanged": self.unchanged,
        }


def diff_snapshots(
    prev: dict[str, dict],
    curr: dict[str, dict],
    *,
    floor_ts: Optional[str] = None,
    deletable: Optional[Callable[[str], bool]] = None,
) -> ReconcileDiff:
    """Diff two `source_id -> fingerprint` maps.

    Args:
        prev: the previously persisted snapshot.
        curr: the snapshot just built from a fresh fetch of the SAME window.
        floor_ts: optional ISO lower bound. A `source_id` present in `prev` but
            absent in `curr` is only reported as deleted when its stored
            timestamp is >= floor_ts. This prevents a narrower current window
            from falsely flagging older messages (outside the window) as
            deleted. Pass the oldest timestamp the current fetch actually
            covered.
        deletable: optional predicate `(source_id) -> bool`. A missing id is
            only reported as deleted when this returns True. Use it to restrict
            deletes to scopes (channels) that were SUCCESSFULLY scanned this
            run — so a transient channel-fetch failure cannot mass-flag that
            channel's messages as deleted. When None, all missing ids are
            eligible (subject to floor_ts).

    Deletion safety: a message is only called deleted when it is inside the
    reconciled window AND its scope was actually scanned. Otherwise it is
    assumed out-of-window or unscanned, not removed (fail-safe: under-report
    deletes rather than fabricate them).
    """
    diff = ReconcileDiff()

    for sid, cur_fp_d in curr.items():
        prev_fp_d = prev.get(sid)
        if prev_fp_d is None:
            diff.added.append(sid)
            continue
        cur_fp = Fingerprint.from_dict(cur_fp_d)
        prev_fp = Fingerprint.from_dict(prev_fp_d)
        if cur_fp.changed_from(prev_fp):
            diff.edited.append(sid)
        else:
            diff.unchanged += 1

    for sid, prev_fp_d in prev.items():
        if sid in curr:
            continue
        # Present before, absent now → candidate delete. Guard with the window
        # floor so out-of-window messages aren't mistaken for deletes...
        if floor_ts is not None:
            ts = (prev_fp_d or {}).get("timestamp") or ""
            if ts and ts < floor_ts:
                continue
        # ...and require the scope to have been successfully scanned, so a
        # failed channel fetch doesn't fabricate deletes for its messages.
        if deletable is not None and not deletable(sid):
            continue
        diff.deleted.append(sid)

    diff.edited.sort()
    diff.deleted.sort()
    diff.added.sort()
    return diff


__all__ = [
    "Fingerprint",
    "fingerprint_event",
    "build_snapshot",
    "ReconcileDiff",
    "diff_snapshots",
]
