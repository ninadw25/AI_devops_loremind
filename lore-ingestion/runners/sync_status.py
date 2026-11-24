"""
runners/sync_status.py
──────────────────────
Live status of the L0 payload sync. Reads progress straight from Postgres
(payload counts + per-mailbox fetch cursors), so it is safe to run anytime while
a sync is in flight — it never touches the fetch or the external API.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.sync_status --org ezobooks [--source gmail]
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import sqlalchemy as sa

from builders.l0_ledger import FetchCursor, SourcePayload, Event, LedgerStore, _resolve_dsn


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def status(org: str, source: str = "gmail") -> None:
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("sync_status: could not connect to Postgres.")
    try:
        with store._session() as s:
            payloads = s.execute(
                sa.select(sa.func.count()).select_from(SourcePayload)
                .where(SourcePayload.org == org, SourcePayload.source == source)).scalar()
            events = s.execute(
                sa.select(sa.func.count()).select_from(Event)
                .where(Event.org == org, Event.source == source)).scalar()
            cursors = s.execute(
                sa.select(FetchCursor.principal, FetchCursor.last_internal_date_ms,
                          FetchCursor.updated_at)
                .where(FetchCursor.org == org, FetchCursor.source == source)
                .order_by(FetchCursor.updated_at.desc())).all()
            # per-mailbox payload counts (from sightings — one mailbox per key)
            newest = s.execute(
                sa.select(sa.func.max(SourcePayload.fetched_at))
                .where(SourcePayload.org == org, SourcePayload.source == source)).scalar()

        print(f"\n  Sync status — org={org} source={source}")
        print(f"  {'─' * 60}")
        print(f"  payloads replicated : {payloads:,}")
        print(f"  events projected    : {events:,}  "
              f"({'in sync' if events == payloads else 'run builders.normalize_events'})")
        print(f"  mailboxes with cursor: {len(cursors)}  (fetched at least once)")
        print(f"  last payload written : {newest.strftime('%Y-%m-%d %H:%M:%S UTC') if newest else '—'}")
        print(f"  {'─' * 60}")
        print(f"  {'mailbox':<38} {'cursor (newest msg)':<18} {'updated'}")
        for principal, ms, updated in cursors:
            upd = updated.strftime("%H:%M:%S") if updated else "—"
            print(f"  {principal:<38} {_fmt_ms(ms):<18} {upd}")
        if not cursors:
            print("  (no mailboxes fetched yet)")
        print()
    finally:
        store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live status of the L0 payload sync")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default="gmail")
    args = ap.parse_args()
    status(args.org, source=args.source)


if __name__ == "__main__":
    main()
