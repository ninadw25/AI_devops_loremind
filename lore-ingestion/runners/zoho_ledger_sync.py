"""
runners/zoho_ledger_sync.py
───────────────────────────
Sync front-end for Zoho Mail — the Zoho twin of `runners/ledger_sync.py`.

Replicates each Zoho account's messages once, losslessly, into the L0
`source_payloads` table, then advances that account's cursor in Postgres —
exactly the sync frame Gmail uses, so everything downstream (normalize → event
→ noise → conversation → claims) is a source-agnostic derivation over the same
replica.

    fetch_mail_for_account (generator) → zoho l0.canonical_payload (lossless) →
    upsert source_payloads → advance fetch_cursors(org, 'zoho_mail', account)

Writes are per-batch, so an interruption costs only the batch in flight; the
cursor moves only after an account is fully replicated, and a resumed run skips
messages the replica already holds. Credentials come from the environment
(ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET / ZOHO_REFRESH_TOKEN) — the control plane
injects the client's, resolved from vault at run launch.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.zoho_ledger_sync --org ezobooks
    ../.venv/bin/python -m runners.zoho_ledger_sync --org ezobooks --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from builders.l0_ledger import LedgerStore, _resolve_dsn
from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.org_config import load_org_config
from infra.zoho import l0 as zoho_l0
from infra.zoho.fetcher import (fetch_mail_accounts, fetch_mail_for_account,
                               get_access_token)
from infra.zoho.normalizer import _actor_from_zoho_address
from pipeline.filter import is_noise
from runners.l0_sync import run_l0_sync

log = get_logger("runners.zoho_ledger_sync")

SOURCE = "zoho_mail"
WRITE_BATCH = 200          # messages buffered before a durable write


def _noise_from_list_row(org: str):
    """A predicate that judges a Zoho LIST ROW as noise, before its body is bought.

    `is_noise` decides on sender domain for every rule but one, and the list
    row already carries `fromAddress` — so the expensive per-message content
    GET can be skipped for the ~70% of mail that is newsletters and machine
    senders. Body is stubbed non-empty deliberately: the empty-body-external
    rule cannot be evaluated before the body exists, and it is re-evaluated for
    real by `builders/classify_noise` over the stored event. This predicate
    only ever skips what that stage would also have dropped.
    """
    cfg = load_org_config(org, get_settings().output_root / "config")
    noise_domains = set(cfg.get("discovered_noise_domains", []))
    allowlist = set(cfg.get("allowlisted_domains", []))

    def _skip(row: dict) -> bool:
        sender = _actor_from_zoho_address(row.get("fromAddress") or "")
        if not (sender and sender.email):
            return False           # unknown sender — buy the body, judge it later
        drop, _ = is_noise(
            {"tags": [], "primary_actor": f"email:{sender.email}", "body": "-"},
            noise_domains, allowlist)
        return drop

    return _skip


def _batched(gen, emit, size: int) -> None:
    """Drain a per-message generator into `emit(batch)` calls of `size`."""
    buf: list[dict] = []
    for msg in gen:
        buf.append(msg)
        if len(buf) >= size:
            emit(buf)
            buf = []
    if buf:
        emit(buf)


def sync(org: str, dry_run: bool = False, limit_accounts: int | None = None,
         output_dir: str = "./output", parallel: int = 8,
         scanback_days: int | None = None, full_bodies: bool = False) -> dict:
    logs_dir = Path(output_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=org, log_dir=str(logs_dir), source="zoho_ledger_sync")

    settings = get_settings()
    token = get_access_token(settings)          # validates ZOHO_* creds up front
    accounts = [
        {"account_id": a.get("accountId") or a.get("account_id"),
         "account_email": a.get("primaryEmailAddress") or a.get("mailboxAddress") or ""}
        for a in fetch_mail_accounts(token, settings)
    ]
    accounts = [a for a in accounts if a["account_id"] and a["account_email"]]
    if limit_accounts:
        accounts = accounts[:limit_accounts]
    if not accounts:
        log.error("[zsync] no Zoho mail accounts visible to this token")
        return {"principals": 0}

    store = None
    if not dry_run:
        store = LedgerStore(_resolve_dsn())
        if not store.connect():
            raise RuntimeError("zoho_ledger_sync: could not connect to Postgres.")

    # Source-specific piece: how to pull one account's mail. Everything else —
    # cursor, per-batch write, parallel, failure isolation — is the shared core.
    skip_content = None if full_bodies else _noise_from_list_row(org)

    def fetch_account(email: str, since_ms, emit) -> None:
        acct = next(a for a in accounts if a["account_email"] == email)
        # Bodies already in the replica must not be bought twice. The cursor
        # only advances on a fully-clean mailbox (l0_sync), so a backfill
        # interrupted at 90% re-lists from the top — without this it would also
        # re-fetch 90% of the bodies, which is the entire cost of the run.
        known = store.known_local_ids(org, SOURCE, email) if store else None
        if known:
            log.info("[zsync] %s — %d messages already replicated, bodies will be skipped",
                     email, len(known))
        # L0 owns the cursor (Postgres), so bypass the legacy JSONL store.
        gen = fetch_mail_for_account(
            org=org, account_id=acct["account_id"], account_email=email,
            settings=settings, since_epoch_ms=since_ms, manage_cursor=False,
            scanback_days=scanback_days, skip_ids=known, skip_content=skip_content)
        _batched(gen, emit, WRITE_BATCH)

    try:
        return run_l0_sync(
            org, SOURCE, accounts, fetch_account, zoho_l0,
            store=store, label=lambda a: a["account_email"],
            dry_run=dry_run, parallel=parallel)
    finally:
        if store:
            store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Zoho Mail → L0 payload replica (sync front-end)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--dry-run", action="store_true", help="Fetch + build, no writes")
    ap.add_argument("--limit-accounts", type=int, default=None, help="Cap account count (smoke runs)")
    ap.add_argument("--output-dir", default="./output")
    ap.add_argument("--parallel", type=int, default=8,
                    help="Accounts fetched concurrently (default 8). Zoho 429s above ~10 "
                         "concurrent requests; a measured run at 4 held ~1085 req/min with "
                         "31 throttles in 5199 requests, so 8 is the safe ceiling, not 10.")
    ap.add_argument("--scanback-days", type=int, default=None,
                    help="How far back a FIRST sync reaches (default: ZOHO_MAIL_SCANBACK_DAYS, "
                         "90). Ignored once a mailbox has a cursor. Use a large value for a "
                         "full historical backfill — 90 days is not 'all the mail'.")
    ap.add_argument("--full-bodies", action="store_true",
                    help="Buy every message body, even ones the sender-domain noise rules "
                         "already condemn (default: skip them, ~70%% fewer requests)")
    args = ap.parse_args()

    totals = sync(org=args.org, dry_run=args.dry_run, limit_accounts=args.limit_accounts,
                  output_dir=args.output_dir, parallel=args.parallel,
                  scanback_days=args.scanback_days, full_bodies=args.full_bodies)
    sys.exit(1 if totals.get("failed") else 0)


if __name__ == "__main__":
    main()
