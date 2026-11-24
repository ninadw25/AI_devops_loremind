"""
runners/slack_reconcile.py
──────────────────────────
The Slack "warm reconcile loop" — detect EDITS and DELETES on a standard
(non-Grid) workspace by snapshot-diffing a bounded recent window.

Why bounded: a single `conversations.history` poll shows the latest text for
edits and silently omits deletes. The only non-Grid way to learn what changed
is to compare against what we saw last time — but we ONLY re-scan a trailing
window (default 30 days), never all of history, because edits/deletes cluster
in recent messages. Cost is O(window), not O(history), so the source is never
overloaded as the workspace grows.

Flow per run:
  1. Load the previously persisted snapshot (source_id -> fingerprint).
  2. Fetch the current window from every channel the bot can read.
  3. Build the current snapshot; diff prev → curr (delete-guarded by the
     window floor so out-of-window messages aren't mistaken for deletes).
  4. Report edits/deletes/adds; optionally write a reconcile delta of changed
     KnowledgeEvents (edits carry the new body; deletes are tombstoned).
  5. Persist the current snapshot as the new baseline.

First run has no prior snapshot → it just establishes the baseline (everything
shows as "added"). Edit/delete a few messages, run again → the diff reports them.

On Enterprise Grid this loop is replaced by the Discovery API change-stream
(deterministic, incremental) — see infra/slack/capabilities.py editdelete_pathway.

Usage:
    ../.venv/bin/python -m runners.slack_reconcile --org techtest --window-days 30
    ../.venv/bin/python -m runners.slack_reconcile --org techtest --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[1] / ".env")
except ImportError:
    pass

from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.reconcile import build_snapshot, diff_snapshots
from common.schemas import KnowledgeEvent
from infra.slack.fetcher import SlackFetcher, SLACK_BASE_URL
from infra.slack.normalizer import normalize_message

log = get_logger("slack.reconcile")


def _snapshot_path(org: str, output_dir: Path) -> Path:
    return output_dir / "slack" / f"{org}_reconcile_snapshot.json"


def _delta_path(org: str, output_dir: Path, run_date: str) -> Path:
    return output_dir / "slack" / f"{org}_reconcile_delta_{run_date}.jsonl"


def _load_snapshot(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("fingerprints", {})


def _save_snapshot(path: Path, fingerprints: dict[str, dict], window_floor: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "window_floor": window_floor,
        "count": len(fingerprints),
        "fingerprints": fingerprints,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _fetch_window_events(
    fetcher: SlackFetcher,
    org: str,
    since: datetime,
    only_channels: Optional[list[str]],
) -> tuple[list[dict], dict[str, dict], set[str]]:
    """Fetch the reconcile window.

    Returns (event_dicts, current_records_by_sid, scanned_channels).
    `scanned_channels` is the set of channel ids that fetched WITHOUT error —
    only those are eligible for delete detection, so a transient channel
    failure can't fabricate deletes for that channel's messages.
    """
    fetcher.validate_auth(required_scopes=["channels:read", "users:read"])
    fetcher.load_user_cache()
    channels = fetcher.list_channels(
        types="public_channel,private_channel",
        exclude_archived=True,
        only_channels=only_channels,
    )
    events: list[dict] = []
    records: dict[str, dict] = {}
    scanned: set[str] = set()
    for channel in channels:
        cid = channel.get("id", "")
        try:
            for msg, replies in fetcher.iter_channel_events(channel, since=since):
                for m in ([msg] + list(replies)) if replies else [msg]:
                    ev = normalize_message(
                        m, channel, org,
                        user_cache=fetcher.user_cache,
                        allowed_bot_ids=fetcher.bot_allowlist,
                    )
                    if ev is None:
                        continue
                    d = {**ev.to_dict()}
                    events.append(d)
                    records[d["source_id"]] = d
            scanned.add(cid)  # only mark scanned on a clean full pass
        except Exception as exc:  # one bad channel shouldn't sink the run
            log.warning("reconcile: channel %s failed (not eligible for delete "
                        "detection this run): %s", cid, exc)
    return events, records, scanned


def reconcile(
    org: str,
    token: str,
    window_days: int,
    output_dir: Path,
    apply: bool = False,
    only_channels: Optional[list[str]] = None,
    base_url: str = SLACK_BASE_URL,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    window_floor = since.isoformat()
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    snap_path = _snapshot_path(org, output_dir)
    prev = _load_snapshot(snap_path)

    fetcher = SlackFetcher(token=token, org=org, base_url=base_url)
    events, records, scanned = _fetch_window_events(fetcher, org, since, only_channels)
    curr = build_snapshot(events)

    is_baseline = not prev
    # A message is delete-eligible only if its channel was scanned cleanly this
    # run (source_id is "<channel_id>:<ts>").
    def _deletable(sid: str) -> bool:
        return sid.split(":", 1)[0] in scanned
    diff = diff_snapshots(prev, curr, floor_ts=window_floor, deletable=_deletable)

    log.info("═" * 55)
    log.info("Slack reconcile — %s  (window: last %dd)", org, window_days)
    log.info("  prior snapshot   : %s", "none (baseline run)" if is_baseline else f"{len(prev)} msgs")
    log.info("  fetched in window: %d msgs", len(curr))
    if is_baseline:
        log.info("  → establishing baseline; edit/delete a message and re-run to see the diff")
    else:
        log.info("  edited           : %d", len(diff.edited))
        log.info("  deleted          : %d", len(diff.deleted))
        log.info("  added (new)      : %d", len(diff.added))
        log.info("  unchanged        : %d", diff.unchanged)
        for sid in diff.edited[:10]:
            body = (records.get(sid, {}).get("body") or "")[:60]
            log.info("    EDIT   %s  %r", sid, body)
        for sid in diff.deleted[:10]:
            log.info("    DELETE %s", sid)

    # Emit a reconcile delta of changed events (for downstream re-extraction).
    if apply and not is_baseline and diff.has_changes:
        delta_path = _delta_path(org, output_dir, run_date)
        delta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(delta_path, "w", encoding="utf-8") as fh:
            for sid in diff.edited:
                rec = records.get(sid)
                if rec:
                    rec = {**rec, "reconcile_action": "edited"}
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for sid in diff.deleted:
                # Tombstone: we no longer have the body; emit a delete marker
                # keyed by source_id so the builder can soft-delete the node.
                fh.write(json.dumps({
                    "source_id": sid, "source": "slack", "org": org,
                    "reconcile_action": "deleted",
                }, ensure_ascii=False) + "\n")
        log.info("  reconcile delta  : %s", delta_path)

    # Carry forward fingerprints for channels that FAILED to scan this run, so
    # a transient failure doesn't drop them from the baseline (which would lose
    # the ability to detect their future edits/deletes). Scanned channels are
    # fully represented by `curr`.
    to_save = dict(curr)
    for sid, fp in prev.items():
        if sid in to_save:
            continue
        if sid.split(":", 1)[0] not in scanned:
            to_save[sid] = fp
    _save_snapshot(snap_path, to_save, window_floor)
    log.info("  snapshot saved   : %d msgs (%d scanned channels)", len(to_save), len(scanned))
    log.info("═" * 55)

    return {
        "baseline": is_baseline,
        "window_floor": window_floor,
        "fetched": len(curr),
        **diff.summary(),
    }


def main(argv: Optional[list[str]] = None) -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(description="Slack edit/delete reconcile loop")
    parser.add_argument("--org", default=s.slack_org)
    parser.add_argument("--token", default=s.slack_token)
    parser.add_argument("--window-days", type=int, default=30,
                        help="Trailing window to reconcile (default 30).")
    parser.add_argument("--apply", action="store_true",
                        help="Write a reconcile delta JSONL of edited/deleted events.")
    parser.add_argument("--channels", nargs="*", default=None)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    if not args.org:
        parser.error("--org is required (or set SLACK_ORG)")
    if not args.token:
        parser.error("--token is required (or set SLACK_TOKEN)")

    output_dir = Path(args.output_dir)
    setup_logging(org=args.org, log_dir=str(output_dir / "logs"), source="slack.reconcile")

    result = reconcile(
        org=args.org,
        token=args.token,
        window_days=args.window_days,
        output_dir=output_dir,
        apply=args.apply,
        only_channels=args.channels,
    )
    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
