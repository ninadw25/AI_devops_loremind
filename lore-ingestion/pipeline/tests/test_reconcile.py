"""
Tests for common/reconcile.py — the snapshot-diff edit/delete detector.
"""

from common.reconcile import (
    Fingerprint,
    build_snapshot,
    diff_snapshots,
    fingerprint_event,
)


def _event(source_id, body, edited_ts="", timestamp="2026-06-01T00:00:00+00:00"):
    raw = {"ts": source_id.split(":")[-1]}
    if edited_ts:
        raw["edited"] = {"user": "U1", "ts": edited_ts}
    return {"source_id": source_id, "body": body, "timestamp": timestamp, "raw": raw}


def test_fingerprint_captures_body_and_edit():
    fp = fingerprint_event(_event("C1:1.1", "hello", edited_ts="9.9"))
    assert fp.source_id == "C1:1.1"
    assert fp.edited_ts == "9.9"
    assert fp.body_hash  # non-empty


def test_no_changes_when_identical():
    events = [_event("C1:1.1", "hello"), _event("C1:1.2", "world")]
    snap = build_snapshot(events)
    diff = diff_snapshots(snap, snap)
    assert not diff.has_changes
    assert diff.unchanged == 2


def test_detects_edit_via_body_change():
    prev = build_snapshot([_event("C1:1.1", "original text")])
    curr = build_snapshot([_event("C1:1.1", "EDITED text", edited_ts="9.9")])
    diff = diff_snapshots(prev, curr)
    assert diff.edited == ["C1:1.1"]
    assert not diff.deleted


def test_detects_edit_via_edited_ts_even_if_text_similar():
    # Same body but a new edited.ts still counts (defensive).
    prev = build_snapshot([_event("C1:1.1", "same body", edited_ts="1.0")])
    curr = build_snapshot([_event("C1:1.1", "same body", edited_ts="2.0")])
    diff = diff_snapshots(prev, curr)
    assert diff.edited == ["C1:1.1"]


def test_detects_delete():
    prev = build_snapshot([_event("C1:1.1", "a"), _event("C1:1.2", "b")])
    curr = build_snapshot([_event("C1:1.1", "a")])
    diff = diff_snapshots(prev, curr)
    assert diff.deleted == ["C1:1.2"]
    assert not diff.edited


def test_detects_added():
    prev = build_snapshot([_event("C1:1.1", "a")])
    curr = build_snapshot([_event("C1:1.1", "a"), _event("C1:1.9", "new")])
    diff = diff_snapshots(prev, curr)
    assert diff.added == ["C1:1.9"]
    assert not diff.deleted


def test_floor_ts_guards_out_of_window_from_false_delete():
    # An old message present in prev but outside the current window must NOT be
    # flagged deleted when a floor is supplied.
    prev = build_snapshot([
        _event("C1:old", "ancient", timestamp="2024-01-01T00:00:00+00:00"),
        _event("C1:new", "recent", timestamp="2026-06-01T00:00:00+00:00"),
    ])
    curr = build_snapshot([_event("C1:new", "recent", timestamp="2026-06-01T00:00:00+00:00")])
    # Without floor: old looks deleted.
    assert diff_snapshots(prev, curr).deleted == ["C1:old"]
    # With floor at the window start: old is out-of-window, not deleted.
    diff = diff_snapshots(prev, curr, floor_ts="2026-05-01T00:00:00+00:00")
    assert diff.deleted == []


def test_deletable_guard_prevents_false_delete_on_unscanned_channel():
    # C2 failed to scan this run → its message must NOT be flagged deleted,
    # even though it's missing from curr.
    prev = build_snapshot([
        _event("C1:1.1", "scanned chan msg"),
        _event("C2:2.1", "unscanned chan msg"),
    ])
    curr = build_snapshot([_event("C1:1.1", "scanned chan msg")])
    scanned = {"C1"}  # C2 not scanned
    diff = diff_snapshots(prev, curr, deletable=lambda sid: sid.split(":", 1)[0] in scanned)
    assert diff.deleted == []  # C2:2.1 NOT a false delete

    # If C1's message genuinely vanishes (C1 was scanned), it IS a delete.
    curr2 = build_snapshot([])
    diff2 = diff_snapshots(prev, curr2, deletable=lambda sid: sid.split(":", 1)[0] in scanned)
    assert diff2.deleted == ["C1:1.1"]  # only the scanned channel's delete


def test_combined_edit_delete_add():
    prev = build_snapshot([
        _event("C1:1", "keep"),
        _event("C1:2", "will edit"),
        _event("C1:3", "will delete"),
    ])
    curr = build_snapshot([
        _event("C1:1", "keep"),
        _event("C1:2", "was edited", edited_ts="9.9"),
        _event("C1:4", "brand new"),
    ])
    diff = diff_snapshots(prev, curr)
    assert diff.edited == ["C1:2"]
    assert diff.deleted == ["C1:3"]
    assert diff.added == ["C1:4"]
    assert diff.unchanged == 1
