"""Outlook connector runner.

Implements the 'fetch' and 'smoke' stages for the Outlook and Teams ingestion pipeline.

Phases implemented here:
- Phase 9: CLI orchestration, fetch stage, smoke stage, dry-run, and output writing.

Usage:
    # Full fetch for an org, limited to 100 total items (mail + teams)
    python -m infra.outlook.run --org <org_name> --stage fetch --limit 100

    # Dry run to see what would be fetched without writing files
    python -m infra.outlook.run --org <org_name> --stage fetch --dry-run

    # Run the smoke tests for the connector
    python -m infra.outlook.run --org <org_name> --stage smoke
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
from common.output_paths import OutlookPaths
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from infra.outlook.discovery import emit_ms365_identities, emit_teams_scope_map, emit_ms365_scope_principals
from infra.outlook.normalizer import normalize_outlook_message, normalize_teams_message
from infra.outlook import fetcher


class JSONLWriter:
    """A simple writer for JSONL files."""

    def __init__(self, path: Path, dry_run: bool = False):
        self.path = path
        self.dry_run = dry_run
        self.count = 0
        if not self.dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Create or truncate the file
            self._file = open(self.path, "w", encoding="utf-8")
        else:
            self._file = None

    def write(self, record: dict[str, Any]) -> None:
        """Writes a single dictionary record to the file."""
        if not self.dry_run and self._file:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1

    def close(self) -> None:
        """Closes the file handle."""
        if self._file:
            self._file.close()


def _get_users(token: str, log) -> list[dict[str, Any]]:
    """Fetches all users from the Graph API."""
    log.info("Discovering users via Graph API...")
    try:
        users_data = fetcher.graph_get("/users", token, params={"$select": "userPrincipalName,displayName,mail"})
        users = users_data if isinstance(users_data, list) else users_data.get("value", [])
        log.info("Discovered %d users.", len(users))
        return users
    except Exception as e:
        log.error("Failed to discover users: %s", e)
        return []


def _run_discover_stage(args: argparse.Namespace, log) -> None:
    """Enumerate MS365 users and Teams channel members; write ACL artifacts.

    Does not fetch mail or Teams messages. Discovery only.
    Writes:
      output/outlook/{org}_ms365_identities.json
      output/outlook/{org}_ms365_teams_scopes.json
    """
    validate_for("outlook_discovery")
    log.info("Starting discover stage for org '%s'.", args.org)

    settings = get_settings()
    try:
        token = fetcher.get_access_token(settings)
        log.info("Successfully obtained authentication token.")
    except RuntimeError as e:
        log.error("Authentication failed: %s", e)
        sys.exit(1)

    paths = OutlookPaths(org=args.org, data_dir=Path(args.output_dir))
    paths.makedirs()

    id_path = emit_ms365_identities(
        org=args.org,
        data_dir=paths.root,
        token=token,
    )
    scopes_path = emit_teams_scope_map(
        org=args.org,
        data_dir=paths.root,
        token=token,
    )

    id_data = json.loads(id_path.read_text(encoding="utf-8"))
    scopes_data = json.loads(scopes_path.read_text(encoding="utf-8"))

    # Emit standard scope_principals contract (for rbac-service + identity resolver).
    sp_path = emit_ms365_scope_principals(org=args.org, data_dir=paths.root)

    log.info("=" * 20 + " Discover Summary " + "=" * 20)
    log.info("Org:                    %s", args.org)
    log.info("Identities:             %d users", id_data.get("count", 0))
    log.info("Teams scopes:           %d users with channel memberships", scopes_data.get("person_count", 0))
    log.info("Identities file:        %s", id_path)
    log.info("Scopes file:            %s", scopes_path)
    log.info("scope_principals file:  %s", sp_path)
    log.info("=" * 58)


def _is_removed(msg: dict[str, Any]) -> bool:
    """True for Graph delta tombstones ({"id": ..., "@removed": {...}}).

    Delta responses include deleted/removed items as tombstones carrying no
    content. They must not reach the normalizer (which would emit an empty
    KnowledgeEvent) — incremental delete handling is a separate concern.
    """
    return isinstance(msg, dict) and "@removed" in msg


def _run_fetch_stage(args: argparse.Namespace, log) -> None:
    """Orchestrates the entire fetch process for Outlook and Teams.

    Mirrors the Gmail/Slack/Jira connector contract: the fetch stage
    NORMALIZES each raw Graph payload into a `KnowledgeEvent` and writes the
    normalized records (plus `embedding_text`) to `output/raw/`. The filter →
    person_registry → extraction stages downstream consume normalized events,
    so normalization must happen here, not in a later stage.

    Output paths are owned by `common.output_paths.OutlookPaths`:
        output/raw/{org}_outlook_mail.jsonl   (Outlook Mail events)
        output/raw/{org}_ms_teams.jsonl       (Teams events)
    These are exactly the paths `runners/outlook_smoke.py` and
    `pipeline/filter.py` read, so fetch output is directly consumable.
    """
    paths = OutlookPaths(org=args.org, data_dir=Path(args.output_dir))
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

    # --- User and Channel Discovery ---
    users = _get_users(token, log)
    if not users:
        log.warning("No users found. Skipping mail fetch.")

    log.info("Discovering Teams channels...")
    try:
        channels = fetcher.fetch_teams_channels(org=args.org, settings=settings)
        log.info("Discovered %d Teams channels.", len(channels))
    except Exception as e:
        log.error("Failed to discover Teams channels: %s", e)
        channels = []

    # --- Item Limiting ---
    # Apply a single limit across all fetched items (mail + teams)
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
    teams_skipped = 0

    # --- Mail Fetch (normalize → KnowledgeEvent → raw JSONL) ---
    mail_writer = JSONLWriter(paths.raw_mail_jsonl, args.dry_run)
    log.info("Fetching Outlook mail...")
    for user in users:
        user_email = user.get("userPrincipalName")
        if not user_email:
            continue
        log.info("Fetching mail for %s...", user_email)
        try:
            for message in limit_wrapper(fetcher.fetch_mail_for_user(
                org=args.org,
                user_email=user_email,
                settings=settings
            )):
                if _is_removed(message):
                    mail_skipped += 1
                    continue
                try:
                    event = normalize_outlook_message(message, args.org, user_email)
                    record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
                    mail_writer.write(record)
                except Exception as norm_err:  # one bad message must not kill the run
                    mail_skipped += 1
                    log.warning("Skipping un-normalizable mail message %s: %s",
                                message.get("id", "<no-id>"), norm_err)
        except Exception as e:
            log.error("Failed to fetch mail for %s: %s", user_email, e)

        if limit and fetched_count >= limit:
            break

    mail_writer.close()
    log.info("Completed Outlook mail fetch. Normalized %d events (%d skipped).",
             mail_writer.count, mail_skipped)

    # --- Teams Message Fetch (normalize → KnowledgeEvent → raw JSONL) ---
    teams_count = 0
    teams_writer = None
    if not (limit and fetched_count >= limit):
        teams_writer = JSONLWriter(paths.raw_teams_jsonl, args.dry_run)
        log.info("Fetching Teams messages...")
        for channel in channels:
            team_id = channel.get('team_id')
            channel_id = channel.get('channel_id')
            team_name = channel.get('team_name')
            channel_name = channel.get('channel_name')
            log.info("Fetching messages for Team '%s', Channel '%s'...", team_name, channel_name)
            try:
                for message in limit_wrapper(fetcher.fetch_channel_messages(
                    org=args.org,
                    team_id=team_id,
                    team_name=team_name,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    settings=settings
                )):
                    if _is_removed(message):
                        teams_skipped += 1
                        continue
                    try:
                        event = normalize_teams_message(message, team_name, channel_name, args.org)
                        record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
                        teams_writer.write(record)
                        teams_count += 1
                    except Exception as norm_err:
                        teams_skipped += 1
                        log.warning("Skipping un-normalizable Teams message %s: %s",
                                    message.get("id", "<no-id>"), norm_err)
            except Exception as e:
                log.error("Failed to fetch messages for channel %s: %s", channel_id, e)

            if limit and fetched_count >= limit:
                break
        teams_writer.close()
        log.info("Completed Teams message fetch. Normalized %d events (%d skipped).",
                 teams_count, teams_skipped)

    total_events = mail_writer.count + teams_count

    log.info("=" * 20 + " Fetch Summary " + "=" * 20)
    log.info("Org: %s", args.org)
    log.info("Dry Run: %s", args.dry_run)
    log.info("Outlook Mail events: %d (%d skipped)", mail_writer.count, mail_skipped)
    log.info("Teams events:        %d (%d skipped)", teams_count, teams_skipped)
    if not args.dry_run:
        log.info("Mail output written to: %s", mail_writer.path)
        if teams_writer is not None:
            log.info("Teams output written to: %s", teams_writer.path)
    log.info("=" * 55)

    log_summary(
        "outlook_fetch",
        org=args.org,
        mail_events=mail_writer.count,
        teams_events=teams_count,
        mail_skipped=mail_skipped,
        teams_skipped=teams_skipped,
        dry_run=args.dry_run,
        mail_output=str(mail_writer.path) if not args.dry_run else None,
        teams_output=str(teams_writer.path) if (teams_writer and not args.dry_run) else None,
    )

    # A 0-event fetch is almost always an upstream auth/scope/discovery bug;
    # fail loudly before the empty artifact cascades into empty filter/registry/
    # extraction stages. --dry-run and --allow-empty downgrade to a no-op.
    check_nonempty(
        "outlook_fetch",
        count=total_events,
        what="normalized events written",
        allow_empty=getattr(args, "allow_empty", False) or args.dry_run,
    )


def _run_smoke_stage(args: argparse.Namespace, log) -> None:
    """Runs the smoke tests for the connector."""
    log.info("Starting smoke stage for org '%s'...", args.org)
    try:
        # Deferred import as requested
        from runners.outlook_smoke import run as run_smoke_tests
        log.info("Successfully imported smoke runner.")
        run_smoke_tests(args)
        log.info("Smoke tests completed.")
    except ImportError:
        log.error("Could not import 'run' from 'runners.outlook_smoke'. Make sure it exists.")
        sys.exit(1)
    except Exception as e:
        log.error("An error occurred during smoke tests: %s", e, exc_info=True)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Outlook and Teams ingestion runner.")
    parser.add_argument("--org", required=True, help="Organization identifier.")
    parser.add_argument("--stage", required=True, choices=["discover", "fetch", "smoke"], help="Execution stage.")
    parser.add_argument("--limit", default=None, help="Maximum number of items to fetch (applied across mail and teams).")
    parser.add_argument("--dry-run", action="store_true", help="Simulate fetch without writing files.")
    parser.add_argument("--output-dir", default="./output", help="Root directory for output files.")
    # ── Smoke-stage options ───────────────────────────────────────────────────
    parser.add_argument(
        "--source",
        choices=["outlook_mail", "ms_teams", "both"],
        default="both",
        help="Which source(s) to smoke-test (default: both). Smoke stage only.",
    )
    parser.add_argument(
        "--raw-mail",
        default=None,
        help="Explicit path to Outlook Mail raw JSONL. "
             "Defaults to output/raw/{org}_outlook_mail.jsonl. Smoke stage only.",
    )
    parser.add_argument(
        "--raw-teams",
        default=None,
        help="Explicit path to Teams raw JSONL. "
             "Defaults to output/raw/{org}_ms_teams.jsonl. Smoke stage only.",
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

    setup_logging(org=args.org, log_dir=str(Path(args.output_dir) / "logs"), source="outlook")
    log = get_logger("outlook.run")

    start_time = time.time()
    log.info("Outlook runner started for stage: %s", args.stage)

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
