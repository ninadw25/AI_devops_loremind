"""Zoho connector runner.

Implements the 'discover', 'fetch', and 'smoke' stages for the Zoho Mail +
Cliq ingestion pipeline. Mirrors infra/outlook/run.py's contract.

Usage:
    # Discover users + Cliq channel ACLs (writes ACL artifacts only)
    python -m infra.zoho.run --org <org_name> --stage discover

    # Full fetch for an org, limited to 100 total items (mail + cliq)
    python -m infra.zoho.run --org <org_name> --stage fetch --limit 100

    # Dry run to see what would be fetched without writing files
    python -m infra.zoho.run --org <org_name> --stage fetch --dry-run

    # Run the smoke tests for the connector
    python -m infra.zoho.run --org <org_name> --stage smoke
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Generator, Iterable
import sys

from common.config import get_settings, validate_for
from common.logger import get_logger, setup_logging
from common.output_paths import ZohoPaths
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from infra.zoho.discovery import emit_zoho_identities, emit_cliq_scope_map, emit_zoho_scope_principals
from infra.zoho.normalizer import normalize_zoho_mail, normalize_cliq_message
from infra.zoho import fetcher


class JSONLWriter:
    """A simple writer for JSONL files."""

    def __init__(self, path: Path, dry_run: bool = False):
        self.path = path
        self.dry_run = dry_run
        self.count = 0
        if not self.dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self.path, "w", encoding="utf-8")
        else:
            self._file = None

    def write(self, record: dict[str, Any]) -> None:
        if not self.dry_run and self._file:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        if self._file:
            self._file.close()


def _run_discover_stage(args: argparse.Namespace, log) -> None:
    """Enumerate Zoho users and Cliq channel members; write ACL artifacts.

    Does not fetch mail or Cliq messages. Discovery only.
    Writes:
      output/zoho/{org}_zoho_identities.json
      output/zoho/{org}_zoho_cliq_scopes.json
      output/zoho/{org}_scope_principals.json
    """
    validate_for("zoho_discovery")
    log.info("Starting discover stage for org '%s'.", args.org)

    settings = get_settings()
    try:
        token = fetcher.get_access_token(settings)
        log.info("Successfully obtained authentication token.")
    except RuntimeError as e:
        log.error("Authentication failed: %s", e)
        sys.exit(1)

    paths = ZohoPaths(org=args.org, data_dir=Path(args.output_dir))
    paths.makedirs()

    id_path = emit_zoho_identities(org=args.org, data_dir=paths.root, token=token)
    scopes_path = emit_cliq_scope_map(org=args.org, data_dir=paths.root, token=token)

    id_data = json.loads(id_path.read_text(encoding="utf-8"))
    scopes_data = json.loads(scopes_path.read_text(encoding="utf-8"))

    sp_path = emit_zoho_scope_principals(org=args.org, data_dir=paths.root)

    log.info("=" * 20 + " Discover Summary " + "=" * 20)
    log.info("Org:                    %s", args.org)
    log.info("Identities:             %d users", id_data.get("count", 0))
    log.info("Cliq scopes:            %d users with channel memberships", scopes_data.get("person_count", 0))
    log.info("Identities file:        %s", id_path)
    log.info("Scopes file:            %s", scopes_path)
    log.info("scope_principals file:  %s", sp_path)
    log.info("=" * 58)


def _get_mail_accounts(token: str, log) -> list[dict[str, Any]]:
    """Fetches all Zoho Mail accounts visible to this token."""
    log.info("Discovering Zoho Mail accounts...")
    try:
        accounts = fetcher.fetch_mail_accounts(token)
        log.info("Discovered %d Mail accounts.", len(accounts))
        return accounts
    except Exception as e:
        log.error("Failed to discover Mail accounts: %s", e)
        return []


def _run_fetch_stage(args: argparse.Namespace, log) -> None:
    """Orchestrates the entire fetch process for Zoho Mail and Cliq.

    Mirrors the Outlook connector contract: the fetch stage NORMALIZES each
    raw payload into a `KnowledgeEvent` and writes the normalized records
    (plus `embedding_text`) to `output/raw/`. The filter -> person_registry
    -> extraction stages downstream consume normalized events, so
    normalization must happen here, not in a later stage.

    Output paths are owned by `common.output_paths.ZohoPaths`:
        output/raw/{org}_zoho_mail.jsonl   (Zoho Mail events)
        output/raw/{org}_zoho_cliq.jsonl   (Cliq events)
    """
    paths = ZohoPaths(org=args.org, data_dir=Path(args.output_dir))
    paths.makedirs()
    log.info("Starting fetch stage for org '%s'. Dry run: %s", args.org, args.dry_run)
    if not args.dry_run:
        log.info("Output directory: %s", paths.data_dir / "raw")

    settings = get_settings()
    try:
        token = fetcher.get_access_token(settings)
        log.info("Successfully obtained authentication token.")
    except RuntimeError as e:
        log.error("Authentication failed: %s", e)
        sys.exit(1)

    accounts = _get_mail_accounts(token, log)
    if not accounts:
        log.warning("No Mail accounts found. Skipping mail fetch.")

    log.info("Discovering Cliq channels...")
    try:
        channels = fetcher.fetch_cliq_channels(token, settings=settings)
        log.info("Discovered %d Cliq channels.", len(channels))
    except Exception as e:
        log.error("Failed to discover Cliq channels: %s", e)
        channels = []

    limit = int(args.limit) if args.limit and int(args.limit) > 0 else None
    fetched_count = 0

    def limit_wrapper(g: Iterable) -> Generator:
        nonlocal fetched_count
        for item in g:
            if limit and fetched_count >= limit:
                log.info("Fetch limit of %d reached.", limit)
                return
            yield item
            fetched_count += 1

    mail_skipped = 0
    cliq_skipped = 0

    # --- Mail Fetch (normalize -> KnowledgeEvent -> raw JSONL) ---
    mail_writer = JSONLWriter(paths.raw_mail_jsonl, args.dry_run)
    log.info("Fetching Zoho Mail...")
    for account in accounts:
        account_id = account.get("accountId") or account.get("account_id")
        account_email = account.get("primaryEmailAddress") or account.get("mailboxAddress") or ""
        if not account_id or not account_email:
            continue
        log.info("Fetching mail for %s...", account_email)
        try:
            for message in limit_wrapper(fetcher.fetch_mail_for_account(
                org=args.org,
                account_id=account_id,
                account_email=account_email,
                settings=settings,
            )):
                try:
                    event = normalize_zoho_mail(message, args.org, account_email)
                    record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
                    mail_writer.write(record)
                except Exception as norm_err:
                    mail_skipped += 1
                    log.warning("Skipping un-normalizable mail message %s: %s",
                                message.get("messageId", "<no-id>"), norm_err)
        except Exception as e:
            log.error("Failed to fetch mail for %s: %s", account_email, e)

        if limit and fetched_count >= limit:
            break

    mail_writer.close()
    log.info("Completed Zoho Mail fetch. Normalized %d events (%d skipped).",
              mail_writer.count, mail_skipped)

    # --- Cliq Message Fetch (normalize -> KnowledgeEvent -> raw JSONL) ---
    cliq_count = 0
    cliq_writer = None
    if not (limit and fetched_count >= limit):
        cliq_writer = JSONLWriter(paths.raw_cliq_jsonl, args.dry_run)
        log.info("Fetching Cliq messages...")
        for channel in channels:
            channel_id = str(channel.get("chat_id") or channel.get("id") or "")
            channel_name = channel.get("name") or channel.get("title") or ""
            if not channel_id:
                continue
            log.info("Fetching messages for Channel '%s'...", channel_name)
            try:
                for message in limit_wrapper(fetcher.fetch_cliq_messages(
                    org=args.org,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    settings=settings,
                )):
                    try:
                        event = normalize_cliq_message(message, channel_name, args.org)
                        record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
                        cliq_writer.write(record)
                        cliq_count += 1
                    except Exception as norm_err:
                        cliq_skipped += 1
                        log.warning("Skipping un-normalizable Cliq message %s: %s",
                                    message.get("id", "<no-id>"), norm_err)
            except Exception as e:
                log.error("Failed to fetch messages for channel %s: %s", channel_id, e)

            if limit and fetched_count >= limit:
                break
        cliq_writer.close()
        log.info("Completed Cliq message fetch. Normalized %d events (%d skipped).",
                  cliq_count, cliq_skipped)

    total_events = mail_writer.count + cliq_count

    log.info("=" * 20 + " Fetch Summary " + "=" * 20)
    log.info("Org: %s", args.org)
    log.info("Dry Run: %s", args.dry_run)
    log.info("Zoho Mail events: %d (%d skipped)", mail_writer.count, mail_skipped)
    log.info("Cliq events:      %d (%d skipped)", cliq_count, cliq_skipped)
    if not args.dry_run:
        log.info("Mail output written to: %s", mail_writer.path)
        if cliq_writer is not None:
            log.info("Cliq output written to: %s", cliq_writer.path)
    log.info("=" * 55)

    log_summary(
        "zoho_fetch",
        org=args.org,
        mail_events=mail_writer.count,
        cliq_events=cliq_count,
        mail_skipped=mail_skipped,
        cliq_skipped=cliq_skipped,
        dry_run=args.dry_run,
        mail_output=str(mail_writer.path) if not args.dry_run else None,
        cliq_output=str(cliq_writer.path) if (cliq_writer and not args.dry_run) else None,
    )

    check_nonempty(
        "zoho_fetch",
        count=total_events,
        what="normalized events written",
        allow_empty=getattr(args, "allow_empty", False) or args.dry_run,
    )


def _run_smoke_stage(args: argparse.Namespace, log) -> None:
    """Runs the smoke tests for the connector."""
    log.info("Starting smoke stage for org '%s'...", args.org)
    try:
        from runners.zoho_smoke import run as run_smoke_tests
        log.info("Successfully imported smoke runner.")
        run_smoke_tests(args)
        log.info("Smoke tests completed.")
    except ImportError:
        log.error("Could not import 'run' from 'runners.zoho_smoke'. Make sure it exists.")
        sys.exit(1)
    except Exception as e:
        log.error("An error occurred during smoke tests: %s", e, exc_info=True)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zoho Mail + Cliq ingestion runner.")
    parser.add_argument("--org", required=True, help="Organization identifier.")
    parser.add_argument("--stage", required=True, choices=["discover", "fetch", "smoke"], help="Execution stage.")
    parser.add_argument("--limit", default=None, help="Maximum number of items to fetch (applied across mail and cliq).")
    parser.add_argument("--dry-run", action="store_true", help="Simulate fetch without writing files.")
    parser.add_argument("--output-dir", default="./output", help="Root directory for output files.")
    parser.add_argument(
        "--source",
        choices=["zoho_mail", "zoho_cliq", "both"],
        default="both",
        help="Which source(s) to smoke-test (default: both). Smoke stage only.",
    )
    parser.add_argument(
        "--raw-mail",
        default=None,
        help="Explicit path to Zoho Mail raw JSONL. "
             "Defaults to output/raw/{org}_zoho_mail.jsonl. Smoke stage only.",
    )
    parser.add_argument(
        "--raw-cliq",
        default=None,
        help="Explicit path to Cliq raw JSONL. "
             "Defaults to output/raw/{org}_zoho_cliq.jsonl. Smoke stage only.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not raise if a pipeline stage produces zero events. Smoke stage only.",
    )
    parser.add_argument(
        "--force-restart",
        action="store_true",
        help="Ignore existing extraction checkpoints and re-extract from scratch. Smoke stage only.",
    )
    args = parser.parse_args()

    setup_logging(org=args.org, log_dir=str(Path(args.output_dir) / "logs"), source="zoho")
    log = get_logger("zoho.run")

    start_time = time.time()
    log.info("Zoho runner started for stage: %s", args.stage)

    if args.stage == "discover":
        _run_discover_stage(args, log)
    elif args.stage == "fetch":
        _run_fetch_stage(args, log)
    elif args.stage == "smoke":
        _run_smoke_stage(args, log)

    duration = time.time() - start_time
    log.info("Runner finished in %.2f seconds.", duration)


if __name__ == "__main__":
    main()
