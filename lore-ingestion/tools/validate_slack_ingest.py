#!/usr/bin/env python3
"""
tools/validate_slack_ingest.py
──────────────────────────────
Validate a Slack ingestion fetch against the generator's ground-truth manifest.

The Slack test-data generator (slack-test-data-generator/) now writes a manifest
of exactly what it created / edited / deleted. This script diffs that ground
truth against the fetched KnowledgeEvent JSONL so we can answer, deterministically:

  • Coverage   — did we capture every message that should still exist?
  • Edits      — does every edited message show is_edited=true with the FINAL text?
  • Deletes    — are deleted messages correctly ABSENT from the fetch?
                 (a single poll cannot see deletes — this quantifies that gap and
                  is the evidence for why the reconcile/snapshot-diff loop exists)

Messages are matched on the stable Slack key `channel_id:ts`, which equals the
KnowledgeEvent `source_id`.

Usage:
    ../.venv/bin/python -m tools.validate_slack_ingest \
        --manifest slack_test_manifest.json \
        --events output/raw/techtest_slack_2026-06-03.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _key(channel_id: str, ts: str) -> str:
    return f"{channel_id}:{ts}"


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_events(path: Path) -> dict[str, dict]:
    """Index fetched events by source_id (channel_id:ts)."""
    events: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        raw = rec.get("raw") or {}
        channel_id = (raw.get("slack_meta") or {}).get("channel_id") or raw.get("channel_id", "")
        ts = raw.get("ts") or ""
        sid = rec.get("source_id") or _key(channel_id, ts)
        if sid:
            events[sid] = rec
    return events


def _is_edited(event: dict) -> bool:
    raw = event.get("raw") or {}
    meta = raw.get("slack_meta") or {}
    return bool(meta.get("is_edited") or ("edited" in raw))


def validate(manifest: dict, events: dict[str, dict]) -> dict:
    created = manifest.get("created", [])
    edited = manifest.get("edited", [])
    deleted = manifest.get("deleted", [])

    deleted_keys = {_key(d["channel_id"], d["ts"]) for d in deleted}
    edited_keys = {_key(e["channel_id"], e["ts"]) for e in edited}

    # ── Coverage: every created-but-not-deleted message should be present ──
    expected_present = [
        c for c in created if _key(c["channel_id"], c["ts"]) not in deleted_keys
    ]
    missing = [
        c for c in expected_present if _key(c["channel_id"], c["ts"]) not in events
    ]

    # ── Edits: present + flagged is_edited + body matches FINAL text ───────
    edit_results = []
    for e in edited:
        k = _key(e["channel_id"], e["ts"])
        ev = events.get(k)
        if k in deleted_keys:
            continue  # edited then deleted → expected absent, checked below
        if ev is None:
            edit_results.append((k, "MISSING", None))
            continue
        flagged = _is_edited(ev)
        body = (ev.get("body") or "").strip()
        final = (e.get("final_text") or "").strip()
        # Slack formatting (```/*/_) may wrap the text; compare on substring of
        # the meaningful core to stay robust to markup the generator added.
        body_ok = final in body or body in final or _core(final) in _core(body)
        status = "OK" if (flagged and body_ok) else (
            "NOT_FLAGGED" if not flagged else "STALE_TEXT"
        )
        edit_results.append((k, status, {"flagged": flagged, "body_ok": body_ok}))

    # ── Deletes: should be ABSENT from a single fetch ─────────────────────
    delete_leaks = [k for k in deleted_keys if k in events]

    return {
        "counts": {
            "manifest_created": len(created),
            "manifest_edited": len(edited),
            "manifest_deleted": len(deleted),
            "fetched_events": len(events),
            "expected_present": len(expected_present),
        },
        "coverage_missing": missing,
        "edit_results": edit_results,
        "delete_leaks": delete_leaks,
        "edited_keys": edited_keys,
    }


def _core(s: str) -> str:
    """Strip common Slack markup + whitespace for tolerant text comparison."""
    return "".join(ch for ch in s if ch.isalnum()).lower()


def _print_report(r: dict) -> bool:
    c = r["counts"]
    print("=" * 60)
    print("Slack ingestion validation — fetch vs ground-truth manifest")
    print("=" * 60)
    print(f"  manifest: created={c['manifest_created']} edited={c['manifest_edited']} "
          f"deleted={c['manifest_deleted']}")
    print(f"  fetched events: {c['fetched_events']}")
    print(f"  expected present (created - deleted): {c['expected_present']}")
    print()

    ok = True

    # Coverage
    missing = r["coverage_missing"]
    if missing:
        ok = False
        print(f"  ✗ COVERAGE: {len(missing)} expected messages MISSING from fetch")
        for m in missing[:10]:
            print(f"      - {_key(m['channel_id'], m['ts'])}  {m.get('text','')[:50]!r}")
    else:
        print(f"  ✓ COVERAGE: all {c['expected_present']} expected messages present")

    # Edits
    edit_results = r["edit_results"]
    bad_edits = [er for er in edit_results if er[1] != "OK"]
    good = len(edit_results) - len(bad_edits)
    if bad_edits:
        ok = False
        print(f"  ✗ EDITS: {good}/{len(edit_results)} ok; {len(bad_edits)} problems")
        for k, status, detail in bad_edits[:10]:
            print(f"      - {k}: {status} {detail or ''}")
    else:
        print(f"  ✓ EDITS: all {len(edit_results)} edited messages flagged with final text")

    # Deletes
    leaks = r["delete_leaks"]
    if leaks:
        # A leak means a 'deleted' message was still returned — only possible if
        # it was deleted AFTER this fetch, or the manifest is stale. Worth flagging.
        print(f"  ⚠ DELETES: {len(leaks)} deleted messages STILL present in fetch")
        print(f"      (expected on a snapshot taken before deletion; otherwise investigate)")
        for k in leaks[:10]:
            print(f"      - {k}")
    else:
        print(f"  ✓ DELETES: all {c['manifest_deleted']} deleted messages absent from fetch")
        print(f"      → single-fetch cannot detect deletes; reconcile/snapshot-diff needed")

    print()
    print("STATUS:", "PASS" if ok else "FAIL")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Slack fetch vs manifest")
    parser.add_argument("--manifest", required=True, help="Ground-truth manifest JSON")
    parser.add_argument("--events", required=True, help="Fetched *_slack_*.jsonl")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    events_path = Path(args.events)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}")
        return 2
    if not events_path.exists():
        print(f"ERROR: events file not found: {events_path}")
        return 2

    manifest = _load_manifest(manifest_path)
    events = _load_events(events_path)
    report = validate(manifest, events)
    ok = _print_report(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
