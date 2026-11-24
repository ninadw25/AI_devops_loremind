"""
gmail/run.py
─────────────
Main entry point for Gmail ingestion pipeline.

Stages:
  1. List domain users (Admin SDK)
  2. Fetch emails per user (batch API, rate-limited, with progress logging)
  3. Deduplicate across inboxes (RFC 2822 Message-ID)
  4. Normalize into KnowledgeEvents (messages + threads)
  5. Write events to disk after EACH USER (checkpoint — safe to resume)

Usage:
  python -m gmail.run --domain ezobooks.in --key google-service-key.json --admin-email gk@ezobooks.in --org ezobooks
  python -m gmail.run ... --since 2025-02-01       # incremental
  python -m gmail.run ... --resume                  # continue from last checkpoint
  python -m gmail.run ... --dry-run                 # count messages only
  python -m gmail.run ... --users-file users.json   # fetch only specific users (JSON list)
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from common.logger import setup_logging, get_logger
from common.org_config import init_org_config, load_org_config
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from infra.gmail.fetcher import GmailFetcher, is_functional_mailbox
from infra.gmail.normalizer import (
    normalize_email_message,
    normalize_email_thread,
    get_message_id_header,
)
from common.schemas import KnowledgeEvent

CHECKPOINT_FILENAME = "{org}_gmail_checkpoint.json"


# ══════════════════════════════════════════════════════════════════════
# CHECKPOINT HELPERS
# ══════════════════════════════════════════════════════════════════════

def load_checkpoint(checkpoint_path: Path) -> dict:
    if checkpoint_path.exists():
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        return data
    return {"completed_users": [], "seen_message_ids": [], "total_events_written": 0}


def save_checkpoint(checkpoint_path: Path, completed_users: list, seen_ids: set, total_written: int):
    data = {
        "completed_users": completed_users,
        "seen_message_ids": list(seen_ids),
        "total_events_written": total_written,
    }
    checkpoint_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_events_to_jsonl(events: list[KnowledgeEvent], path: Path):
    """Append events to the output JSONL file (one JSON object per line)."""
    with open(path, "a", encoding="utf-8") as f:
        for event in events:
            record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Lore Gmail Ingestion Pipeline")
    parser.add_argument("--domain",      required=True, help="Google Workspace domain (e.g. ezobooks.in)")
    parser.add_argument("--key",         required=True, help="Path to Google service account JSON key")
    parser.add_argument("--admin-email", required=True, help="Super Admin email for DWD (e.g. gk@ezobooks.in)")
    parser.add_argument("--org",         required=True, help="Organization name (e.g. ezobooks)")
    parser.add_argument("--since",       default=None,  help="ISO date — only fetch emails after this (e.g. 2025-02-01)")
    parser.add_argument("--output-dir",  default="./output", help="Output directory (default: ./output)")
    parser.add_argument("--dry-run",     action="store_true", help="Only count messages, don't fetch full content")
    parser.add_argument("--resume",      action="store_true", help="Resume from last checkpoint (skip completed users)")
    parser.add_argument("--org-domains", default=None, nargs="*",
                        help="Additional org domains for internal detection (e.g. ezobanks.com)")
    parser.add_argument("--extra-functional", default=None, nargs="*",
                        help="Additional email local parts to treat as functional (e.g. dhandaai)")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if 0 events were written.")
    parser.add_argument("--users-file", default=None,
                        help="JSON file with specific users to fetch (list of {email, name, ...}). "
                             "Skips domain listing — only fetches these users. Appends to existing output.")
    parser.add_argument("--use-watermark", action="store_true",
                        help="Incremental sync: read per-user since_epoch from the org's "
                             "watermark file (output/checkpoints/{org}_sync.json) so each "
                             "mailbox resumes from its own last-seen internalDate. The "
                             "watermark itself is NOT advanced here — runners/sync.py "
                             "advances it after the full pipeline succeeds.")
    parser.add_argument("--watermark-out", default=None,
                        help="Path to write the per-user max(internalDate) report after a "
                             "successful fetch. Consumed by runners/sync.py to advance "
                             "the watermark atomically when the rest of the pipeline finishes.")
    args = parser.parse_args()

    # ── Initialize ────────────────────────────────────────────────────
    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    config_dir = output_dir / "config"
    logs_dir = output_dir / "logs"
    checkpoints_dir = output_dir / "checkpoints"
    for d in (raw_dir, config_dir, logs_dir, checkpoints_dir):
        d.mkdir(parents=True, exist_ok=True)

    setup_logging(org=args.org, log_dir=str(logs_dir), source="gmail")
    log = get_logger("gmail.run")

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pipeline_start = time.time()

    events_file      = raw_dir / f"{args.org}_gmail_{run_date}.jsonl"
    checkpoint_path  = checkpoints_dir / CHECKPOINT_FILENAME.format(org=args.org)

    # ── Initialize org config ─────────────────────────────────────────
    org_config = init_org_config(
        org=args.org,
        primary_domain=args.domain,
        config_dir=config_dir,
        extra_domains=args.org_domains,
        extra_functional=args.extra_functional,
    )
    org_domains = org_config.get("org_domains", [args.domain])
    extra_func = set(org_config.get("extra_functional_mailboxes", []))

    log.info("═" * 55)
    log.info("Gmail Ingestion Pipeline — %s", args.org)
    log.info("Domain: %s | Admin: %s", args.domain, args.admin_email)
    log.info("Org domains: %s", ", ".join(org_domains))
    if args.dry_run:
        log.info("Mode: DRY RUN")
    elif args.users_file:
        log.info("Mode: TARGETED fetch from %s (resume-aware)", args.users_file)
    elif args.resume:
        log.info("Mode: RESUME from checkpoint")
    else:
        log.info("Mode: %s", f"incremental (since {args.since})" if args.since else "full historical import")
    log.info("═" * 55)

    fetcher = GmailFetcher(
        service_key_path=args.key,
        domain=args.domain,
        admin_email=args.admin_email,
    )

    # ══════════════════════════════════════════════════════════════════
    # STAGE 1: List users (or load from --users-file)
    # ══════════════════════════════════════════════════════════════════
    log.info("")
    if args.users_file:
        log.info("Stage 1/4: Loading targeted user list from %s...", args.users_file)
        users_file_path = Path(args.users_file)
        if not users_file_path.exists():
            log.error("Users file not found: %s. Aborting.", args.users_file)
            return
        users = json.loads(users_file_path.read_text(encoding="utf-8"))
        log.info("Loaded %d users from %s", len(users), args.users_file)
        # Mark functional status if missing
        for u in users:
            if "is_functional" not in u:
                u["is_functional"] = is_functional_mailbox(u["email"])
    else:
        log.info("Stage 1/4: Listing domain users...")
        users = fetcher.list_users(extra_functional=extra_func or None)

    if not users:
        log.error("No active users found. Aborting.")
        return

    users_file = config_dir / f"{args.org}_gmail_users.json"
    if not args.users_file:
        users_file.write_text(json.dumps(users, indent=2), encoding="utf-8")
        log.info("Saved user list → %s", users_file)

    # ── Dry run ───────────────────────────────────────────────────────
    if args.dry_run:
        log.info("")
        log.info("Stage 2/4: DRY RUN — counting messages per user...")
        counts = fetcher.dry_run_counts(users, since=args.since, org_domains=args.org_domains)
        stats_file = config_dir / f"{args.org}_gmail_dryrun.json"
        stats_file.write_text(json.dumps({
            "org": args.org, "domain": args.domain, "date": run_date,
            "mode": "dry_run", "since": args.since, "users": counts,
        }, indent=2), encoding="utf-8")
        total = sum(r["message_count"] for r in counts if r["message_count"] > 0)
        log.info("")
        log.info("═" * 55)
        log.info("DRY RUN COMPLETE — %s", args.org)
        log.info("  Users         : %d", len(users))
        log.info("  Total messages: %d", total)
        log.info("  Stats saved   : %s", stats_file)
        log.info("═" * 55)
        return

    # ══════════════════════════════════════════════════════════════════
    # STAGE 2: Load checkpoint (if resuming)
    # ══════════════════════════════════════════════════════════════════
    checkpoint = load_checkpoint(checkpoint_path)
    completed_users: list[str] = checkpoint["completed_users"]
    seen_message_ids: set[str] = set(checkpoint["seen_message_ids"])
    total_events_written: int = checkpoint["total_events_written"]

    if (args.resume or args.users_file) and completed_users:
        log.info("")
        log.info("RESUME: %d users already done, %d message IDs seen, %d events written",
                 len(completed_users), len(seen_message_ids), total_events_written)
        if not args.users_file:
            log.info("Skipping: %s", ", ".join(completed_users))
    elif not args.resume and not args.users_file and checkpoint_path.exists():
        # Fresh run — clear old checkpoint and output
        log.info("Fresh run — clearing old checkpoint and output file")
        checkpoint_path.unlink()
        if events_file.exists():
            events_file.unlink()
        completed_users = []
        seen_message_ids = set()
        total_events_written = 0

    users_to_process = [u for u in users if u["email"] not in completed_users]
    log.info("")
    log.info("Stage 2/4: Fetching emails for %d/%d users...", len(users_to_process), len(users))

    # ── Watermark (per-user since_epoch) ──────────────────────────────
    # Loaded once at start; used to drive lossless incremental sync.
    # The watermark is NOT advanced here — only after the full pipeline
    # (extract → validate → upsert) succeeds, runners/sync.py advances it.
    watermark_state = {}
    if args.use_watermark:
        from common.watermark import load as load_watermark
        watermark_state = load_watermark(args.org)
        n_users_with_watermark = sum(
            1 for u in users_to_process
            if (watermark_state.get("users") or {}).get(u["email"])
        )
        log.info("Watermark mode: %d/%d users have a saved since_epoch",
                 n_users_with_watermark, len(users_to_process))

    # ══════════════════════════════════════════════════════════════════
    # STAGE 3: Fetch + dedup + normalize per user, write after each
    # ══════════════════════════════════════════════════════════════════
    user_stats = []
    thread_accumulator: dict[str, list[KnowledgeEvent]] = {}
    # Per-user max(internalDate) observed this run — used to advance
    # the watermark (in runners/sync.py) only after full pipeline success.
    user_max_internal_date_ms: dict[str, int] = {}
    # Per-message normalization failures (F3 — silent truncation gap)
    normalize_failures = 0

    for i, user in enumerate(users_to_process, 1):
        email = user["email"]
        global_index = len(completed_users) + i

        # ── Per-user since_epoch from watermark (lossless resume) ────
        since_epoch = None
        if args.use_watermark:
            from common.watermark import get_user_since_epoch
            since_epoch = get_user_since_epoch(watermark_state, email)
            if since_epoch is not None:
                log.info("[%d/%d] %s — resuming from internalDate > %d",
                         global_index, len(users), email, since_epoch)

        # ── Fetch ────────────────────────────────────────────────────
        user_is_functional = user.get("is_functional", is_functional_mailbox(email))
        raw_messages = fetcher.fetch_user_messages(
            email=email,
            since=args.since,
            since_epoch=since_epoch,
            user_index=global_index,
            total_users=len(users),
            is_functional=user_is_functional,
            org_domains=args.org_domains,
        )

        # ── Dedup + normalize ─────────────────────────────────────────
        fetched = len(raw_messages)
        new_count = 0
        dupe_count = 0
        user_events: list[KnowledgeEvent] = []

        # Track per-user max(internalDate). Watermark math uses ALL fetched
        # messages (not only post-dedup new ones) so the watermark advances
        # correctly even when a run sees only already-seen messages.
        user_max_ms = user_max_internal_date_ms.get(email, 0)

        for msg in raw_messages:
            # Update watermark candidate (Gmail returns internalDate as ms string)
            try:
                ms = int(msg.get("internalDate") or 0)
                if ms > user_max_ms:
                    user_max_ms = ms
            except (TypeError, ValueError):
                pass

            msg_header_id = get_message_id_header(msg) or f"gmail:{msg.get('id', '')}"

            if msg_header_id in seen_message_ids:
                dupe_count += 1
                continue

            seen_message_ids.add(msg_header_id)
            new_count += 1

            try:
                event = normalize_email_message(msg, args.org, email)
                user_events.append(event)

                thread_id = msg.get("threadId", "")
                if thread_id:
                    thread_accumulator.setdefault(thread_id, []).append(event)
            except Exception as e:
                normalize_failures += 1
                log.error("[%d/%d] Failed to normalize message %s: %s",
                          global_index, len(users), msg.get("id", "?"), e)

        if user_max_ms > 0:
            user_max_internal_date_ms[email] = user_max_ms

        log.info("[%d/%d] %s — %d fetched, %d new, %d duplicates, %d events",
                 global_index, len(users), email, fetched, new_count, dupe_count, len(user_events))

        # ── Write events to disk immediately ─────────────────────────
        if user_events:
            append_events_to_jsonl(user_events, events_file)
            total_events_written += len(user_events)
            log.info("[%d/%d] Wrote %d events → %s (total so far: %d)",
                     global_index, len(users), len(user_events), events_file, total_events_written)

        # ── Save checkpoint ───────────────────────────────────────────
        completed_users.append(email)
        save_checkpoint(checkpoint_path, completed_users, seen_message_ids, total_events_written)
        log.info("[%d/%d] Checkpoint saved (%d users done)", global_index, len(users), len(completed_users))

        user_stats.append({
            "email": email, "name": user["name"],
            "is_functional": user_is_functional,
            "fetched": fetched, "new": new_count,
            "duplicates": dupe_count, "events_written": len(user_events),
        })

        # Cooldown between users
        if i < len(users_to_process):
            log.debug("Cooldown: sleeping %.1fs before next user...", fetcher.user_cooldown)
            time.sleep(fetcher.user_cooldown)

    # ══════════════════════════════════════════════════════════════════
    # STAGE 4: Thread aggregation (only for threads with 2+ messages)
    # ══════════════════════════════════════════════════════════════════
    log.info("")
    log.info("Stage 3/4: Aggregating email threads...")

    thread_events: list[KnowledgeEvent] = []
    for thread_id, thread_messages in thread_accumulator.items():
        if len(thread_messages) > 1:
            thread_event = normalize_email_thread(thread_messages, thread_id, args.org)
            if thread_event:
                thread_events.append(thread_event)

    if thread_events:
        append_events_to_jsonl(thread_events, events_file)
        total_events_written += len(thread_events)
        log.info("Wrote %d thread events → %s", len(thread_events), events_file)

    log.info("Thread events: %d", len(thread_events))

    # ── Final stats file ──────────────────────────────────────────────
    log.info("")
    log.info("Stage 4/4: Writing stats...")

    duration = time.time() - pipeline_start
    total_fetched = sum(s["fetched"] for s in user_stats)
    total_dupes = sum(s["duplicates"] for s in user_stats)
    total_message_events = total_events_written - len(thread_events)

    stats = {
        "org": args.org, "domain": args.domain,
        "run_date": run_date,
        "mode": f"incremental (since {args.since})" if args.since else "full",
        "resumed": args.resume,
        "duration_seconds": round(duration, 1),
        "users_processed": len(users_to_process),
        "messages_fetched": total_fetched,
        "duplicates_skipped": total_dupes,
        "message_events": total_message_events,
        "thread_events": len(thread_events),
        "total_events": total_events_written,
        "per_user": user_stats,
    }

    stats_file = config_dir / f"{args.org}_gmail_stats.json"
    stats_file.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    # ── Watermark report (consumed by runners/sync.py) ────────────────
    # We emit max(internalDate) per user that this fetch observed. The
    # report is consumed by runners/sync.py AFTER the rest of the
    # pipeline succeeds — only then is the org's watermark advanced.
    # This guarantees lossless continuation across crash boundaries.
    if args.watermark_out:
        watermark_report = {
            "org": args.org,
            "run_date": run_date,
            "user_max_internal_date_ms": user_max_internal_date_ms,
        }
        Path(args.watermark_out).write_text(
            json.dumps(watermark_report, indent=2), encoding="utf-8"
        )
        log.info("Watermark report → %s (%d users)",
                 args.watermark_out, len(user_max_internal_date_ms))

    # Clear checkpoint on successful completion (only for full runs, not targeted)
    if not args.users_file and checkpoint_path.exists():
        checkpoint_path.unlink()
        log.info("Checkpoint cleared (run complete)")

    # ── Summary ───────────────────────────────────────────────────────
    minutes = int(duration // 60)
    seconds = int(duration % 60)

    log.info("")
    log.info("═" * 55)
    log.info("DONE — %s Gmail ingestion complete", args.org)
    log.info("  Users processed : %d", len(users_to_process))
    log.info("  Messages fetched: %d", total_fetched)
    log.info("  Duplicates      : %d", total_dupes)
    log.info("  Events created  : %d (%d messages + %d threads)",
             total_events_written, total_message_events, len(thread_events))
    log.info("  Duration        : %dm %ds", minutes, seconds)
    log.info("  Output          : %s", events_file)
    log.info("  Stats           : %s", stats_file)
    log.info("  Log             : %s/%s_gmail_%s.log", logs_dir, args.org, run_date)
    log.info("═" * 55)

    # ── Failed-users audit (F2 from audit) ────────────────────────
    # A "failed" user is one that completed without writing any events.
    # Could be: no Gmail data, all duplicates, or a fetch error path
    # that wasn't surfaced (network errors are warned but swallowed).
    failed_users = [
        {"email": s["email"], "name": s["name"],
         "fetched": s["fetched"], "duplicates": s["duplicates"]}
        for s in user_stats if s["events_written"] == 0
    ]
    failed_users_file = config_dir / f"{args.org}_gmail_failed_users.json"
    failed_users_file.write_text(
        json.dumps(failed_users, indent=2), encoding="utf-8")
    if failed_users:
        log.warning("%d user(s) produced 0 events — audit: %s",
                    len(failed_users), failed_users_file)

    if normalize_failures:
        log.warning("%d message(s) failed to normalize (skipped)",
                    normalize_failures)

    log_summary(
        "gmail_fetch",
        org=args.org,
        users=len(users_to_process),
        messages=total_fetched,
        duplicates=total_dupes,
        events=total_events_written,
        thread_events=len(thread_events),
        normalize_failures=normalize_failures,
        failed_users=len(failed_users),
        duration_s=round(duration, 1),
    )
    check_nonempty("gmail_fetch", count=total_events_written,
                   what="events written",
                   allow_empty=getattr(args, "allow_empty", False))

    log.info("")
    log.info("Next step: Feed the .jsonl file into your vector DB.")


if __name__ == "__main__":
    main()
