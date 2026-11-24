"""
runners/ledger_sync.py
──────────────────────
Sync front-end for Gmail — build step 1a of 05_SYNC_AND_LEDGER.md.

Replaces the JSONL relay (fetch → normalize-at-fetch → filter → files) with the
sync frame: replicate each mailbox's messages once, losslessly, into the L0
payload table, then advance that mailbox's cursor.

    fetch batch (GmailFetcher) → canonical_payload (lossless) →
    upsert source_payloads (+ merge cross-mailbox sightings) → … → cursor

Writes are per-batch, so an interruption costs only the batch in flight; the
cursor moves only when a mailbox is fully replicated, and a resumed run skips
messages the replica already holds.

Everything downstream (normalize → event, noise class, threading, claims) is a
derivation over this replica, added in build steps 1b–1d. This runner writes the
payload floor only; it does not touch the old JSONL path, which still works until
1b proves events are reproducible from the replica.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.ledger_sync --org ezobooks --domain ezobooks.in \
        --admin-email gauravkumar@ezobooks.in --key key.json \
        [--users-file users.json] [--since 2026-01-01] [--limit-users N] [--dry-run]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from builders.l0_ledger import LedgerStore, _resolve_dsn
from common.logger import get_logger, setup_logging
from common.org_config import init_org_config
from infra.gmail import normalizer as gmail_l0
from infra.gmail.fetcher import GmailFetcher, is_functional_mailbox
from runners.l0_sync import run_l0_sync

log = get_logger("runners.ledger_sync")

SOURCE = "gmail"


def sync(org: str, domain: str, admin_email: str, key: str,
         users: list[dict] | None = None, since: str | None = None,
         limit_users: int | None = None, dry_run: bool = False,
         output_dir: str = "./output", parallel: int = 8) -> dict:
    config_dir = Path(output_dir) / "config"
    logs_dir = Path(output_dir) / "logs"
    for d in (config_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    setup_logging(org=org, log_dir=str(logs_dir), source="ledger_sync")

    org_config = init_org_config(org=org, primary_domain=domain, config_dir=config_dir)
    extra_func = set(org_config.get("extra_functional_mailboxes", []))

    fetcher = GmailFetcher(service_key_path=key, domain=domain, admin_email=admin_email)
    if users is None:
        users = fetcher.list_users(extra_functional=extra_func or None)
    if limit_users:
        users = users[:limit_users]
    if not users:
        log.error("[sync] no users to fetch")
        return {"principals": 0}

    store = None
    if not dry_run:
        store = LedgerStore(_resolve_dsn())
        if not store.connect():
            raise RuntimeError("ledger_sync: could not connect to Postgres.")

    total = len(users)

    # Source-specific piece: how to pull one mailbox. Gmail-specific concerns —
    # DWD, since-date, seconds-not-ms cursor, and intra-mailbox skip_ids resume —
    # all live here; the shared core (runners/l0_sync) owns everything else.
    def fetch_mailbox(email: str, since_ms, emit) -> None:
        i = next(idx for idx, u in enumerate(users, 1) if u["email"] == email)
        user = users[i - 1]
        is_func = user.get("is_functional", is_functional_mailbox(email))
        since_epoch = (since_ms // 1000) if since_ms else None
        # Resume INSIDE a mailbox too: skip messages already held.
        skip_ids = store.known_local_ids(org, SOURCE, email) if store is not None else None
        fetcher.fetch_user_messages(
            email=email, since=since, since_epoch=since_epoch,
            user_index=i, total_users=total,
            is_functional=is_func, org_domains=org_config.get("org_domains"),
            on_batch=emit, skip_local_ids=skip_ids)

    try:
        # Cross-mailbox parallelism is THE path (docs/PARALLEL_FETCH.md): each
        # mailbox has its OWN 250-unit/s quota bucket, so N workers cost nothing
        # in contention. The shared core isolates one bad mailbox's failure.
        return run_l0_sync(
            org, SOURCE, users, fetch_mailbox, gmail_l0,
            store=store, label=lambda u: u["email"],
            dry_run=dry_run, parallel=parallel)
    finally:
        if store:
            store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Gmail → L0 payload replica (sync front-end)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--admin-email", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--users-file", default=None, help="JSON list of {email, name}; skips domain listing")
    ap.add_argument("--since", default=None, help="ISO date — only fetch after this (first run only)")
    ap.add_argument("--limit-users", type=int, default=None, help="Cap user count (smoke runs)")
    ap.add_argument("--dry-run", action="store_true", help="Fetch + build, no writes")
    ap.add_argument("--output-dir", default="./output")
    ap.add_argument("--parallel", type=int, default=8,
                    help="Mailboxes fetched concurrently (default 8; each mailbox "
                         "has its own quota bucket — 1 = debug a mailbox in order)")
    args = ap.parse_args()

    import json
    import sys
    users = None
    if args.users_file:
        users = json.loads(Path(args.users_file).read_text(encoding="utf-8"))

    totals = sync(org=args.org, domain=args.domain, admin_email=args.admin_email, key=args.key,
                  users=users, since=args.since, limit_users=args.limit_users,
                  dry_run=args.dry_run, output_dir=args.output_dir, parallel=args.parallel)
    # Exit non-zero if any mailbox failed, so a restart wrapper retries them
    # (their cursors didn't advance, so retry is a cheap delta for the rest).
    sys.exit(1 if totals.get("failed") else 0)


if __name__ == "__main__":
    main()
