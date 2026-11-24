"""
infra/slack/run.py
CLI entrypoint adapted from slack-connector; imports rewritten to package-relative.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parents[2] / ".env")
except ImportError:
    pass

from common.config import get_settings
from common.logger import setup_logging, get_logger
from common.schemas import KnowledgeEvent
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from .fetcher import SlackFetcher, SLACK_BASE_URL
from .capabilities import AclPathway
from .admin import SlackAdminClient
from .discovery import main as discovery_main
from .normalizer import (
    build_knowledge_event,
    normalize_message,
    truncate_thread_for_llm,
    classify_thread,
)
from .socket_mode import SlackSocketModeIngestor

CHECKPOINT_FILENAME = "{org}_slack_checkpoint.json"


def load_checkpoint(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"completed_channels": [], "seen_event_ids": [], "total_events_written": 0, "last_run_at": ""}


def save_checkpoint(path: Path, completed_channels: list[str], seen_ids: set[str], total_written: int) -> None:
    data = {"completed_channels": completed_channels, "seen_event_ids": list(seen_ids), "total_events_written": total_written, "last_run_at": datetime.now(timezone.utc).isoformat()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_events(events: list[KnowledgeEvent], path: Path) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for event in events:
            record = {**event.to_dict(), "embedding_text": event.to_embedding_text()}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_channel(fetcher: SlackFetcher, channel: dict, org: str, since: Optional[datetime], dry_run: bool, seen_ids: set[str], log, noise_counter: Optional[dict[str, int]] = None) -> list[KnowledgeEvent]:
    channel_id = channel["id"]
    channel_name = channel.get("name") or channel_id
    events: list[KnowledgeEvent] = []

    for msg, replies in fetcher.iter_channel_events(channel, since=since):
        user_cache = fetcher.user_cache

        if replies:
            parent = replies[0] if replies else msg
            reply_msgs = replies[1:] if replies else []

            parent_event = normalize_message(parent, channel, org, user_cache, allowed_bot_ids=fetcher.bot_allowlist, noise_counter=noise_counter)
            if parent_event and parent_event.id not in seen_ids:
                events.append(parent_event)
                seen_ids.add(parent_event.id)

            if parent_event:
                node_type = classify_thread(parent.get("text", ""), reply_msgs)
                if node_type not in parent_event.tags:
                    parent_event.tags.append(node_type)

            for reply in reply_msgs:
                reply_event = normalize_message(reply, channel, org, user_cache, allowed_bot_ids=fetcher.bot_allowlist, noise_counter=noise_counter)
                if reply_event and reply_event.id not in seen_ids:
                    events.append(reply_event)
                    seen_ids.add(reply_event.id)

        else:
            event = normalize_message(msg, channel, org, user_cache, allowed_bot_ids=fetcher.bot_allowlist, noise_counter=noise_counter)
            if event and event.id not in seen_ids:
                events.append(event)
                seen_ids.add(event.id)

    return events


def _channel_types(include_private: bool = True, include_dms: bool = False) -> str:
    types = ["public_channel", "private_channel"] if include_private else ["public_channel"]
    if include_dms:
        types.extend(["im", "mpim"])
    return ",".join(types)


def main() -> None:
    s = get_settings()
    parser = argparse.ArgumentParser(description="Lore Slack Ingestion Pipeline", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", default="full", choices=["full", "discovery"], help="Run only discovery or the full pipeline")
    parser.add_argument("--org", default=s.slack_org, help="Organization identifier (e.g. loremind)")
    parser.add_argument("--token", default=s.slack_token, help="Slack token (xoxp- admin user token or xoxb- bot token)")
    parser.add_argument("--app-token", default=s.slack_app_token, help="Slack app token (xapp-...)")
    parser.add_argument("--mode", default=s.slack_ingest_mode.lower(), choices=["polling", "socket", "hybrid"])
    parser.add_argument("--since", default=None, help="ISO date — only fetch messages after this date (e.g. 2025-01-01).")
    parser.add_argument("--output-dir", default="./output", help="Root output directory (default: ./output).")
    parser.add_argument("--dry-run", action="store_true", help="Discover channels and count messages only — do not write output.")
    parser.add_argument("--allow-empty", action="store_true", help="Do not fail if 0 events are written (intentional dry/empty runs).")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint (skip already-completed channels).")
    parser.add_argument("--channels", nargs="*", default=None, help="Specific channel IDs or names to ingest (default: all accessible).")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--include-dms", action="store_true")
    parser.add_argument("--no-auto-join", action="store_true",
                        help="Do not self-join public channels before reading "
                             "(by default the bot joins public channels it isn't "
                             "a member of, since conversations.history requires "
                             "membership).")
    parser.add_argument("--include-private", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--base-url", default=SLACK_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--mock-mode", action="store_true", help="Use mock data from seed_data.json instead of real API")
    parser.add_argument("--mock-seed", default=None, help="Path to seed_data.json (default: mock_testing/seed_data.json)")
    args = parser.parse_args()

    if not args.org:
        parser.error("--org is required (or set SLACK_ORG env var)")
    if not args.mock_mode and not args.token:
        parser.error("--token is required (or set SLACK_TOKEN env var)")
    if args.mode in {"socket", "hybrid"} and not args.app_token:
        parser.error("--app-token is required for socket or hybrid mode (or set SLACK_APP_TOKEN env var)")

    if args.stage == "discovery":
        discovery_main([
            "--org", args.org,
            "--token", args.token,
            "--output-dir", args.output_dir,
            "--base-url", args.base_url,
            "--log-level", args.log_level,
        ] + (["--include-archived"] if args.include_archived else []) + (["--channels", *args.channels] if args.channels else []))
        return

    output_dir = Path(args.output_dir)
    raw_dir = output_dir / "raw"
    checkpoints_dir = output_dir / "checkpoints"
    logs_dir = output_dir / "logs"
    for d in (raw_dir, checkpoints_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    setup_logging(org=args.org, log_dir=str(logs_dir), source="slack")
    log = get_logger("slack.run")

    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    events_file = raw_dir / f"{args.org}_slack_{run_date}.jsonl"
    checkpoint_path = checkpoints_dir / CHECKPOINT_FILENAME.format(org=args.org)

    since: Optional[datetime] = None
    if args.since:
        try:
            since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"--since must be ISO date like 2025-01-01, got: {args.since}")

    socket_ingestor = None
    if args.mode in {"socket", "hybrid"}:
        socket_ingestor = SlackSocketModeIngestor(bot_token=args.token, app_token=args.app_token, org=args.org, base_url=args.base_url, output_file=events_file, only_channels=args.channels, exclude_archived=not args.include_archived, include_private=args.include_private, include_dms=args.include_dms)

    checkpoint = load_checkpoint(checkpoint_path)
    completed_channels: list[str] = checkpoint["completed_channels"]
    seen_ids: set[str] = set(checkpoint.get("seen_event_ids", []))
    total_written: int = checkpoint.get("total_events_written", 0)

    if args.resume:
        log.info("Resuming from checkpoint — %d channels done, %d events written", len(completed_channels), total_written)

    log.info("═" * 55)
    log.info("Slack Ingestion Pipeline — %s", args.org)
    if since:
        log.info("Mode: incremental (since %s)", args.since)
    elif args.resume:
        log.info("Mode: resume from checkpoint")
    elif args.dry_run:
        log.info("Mode: DRY RUN")
    else:
        log.info("Mode: full historical import")
    if args.mode == "socket":
        log.info("Realtime transport: Socket Mode")
    elif args.mode == "hybrid":
        log.info("Realtime transport: polling backfill + Socket Mode")
    log.info("═" * 55)

    if args.mode == "socket" and socket_ingestor is not None:
        log.info("")
        log.info("Starting Socket Mode listener…")
        socket_ingestor.run()
        return

    pipeline_start = time.time()

    # Use mock fetcher if --mock-mode is set
    if args.mock_mode:
        from pathlib import Path as _Path
        from .mock_fetcher import MockSlackFetcher
        seed_path = _Path(args.mock_seed) if args.mock_seed else _Path(__file__).parents[2] / "mock_testing" / "seed_data.json"
        log.info("Mock mode enabled — loading seed data from %s", seed_path)
        fetcher = MockSlackFetcher(seed_path=seed_path, org=args.org)
    else:
        fetcher = SlackFetcher(token=args.token, org=args.org, base_url=args.base_url)

    try:
        fetcher.validate_auth(required_scopes=["channels:read", "users:read"])
    except RuntimeError as exc:
        log.error("Auth failed: %s", exc)
        sys.exit(1)

    log.info("Auth OK — team=%s", fetcher.team_name)

    caps = fetcher.capabilities
    is_admin_path = caps is not None and caps.acl_pathway == AclPathway.ADMIN_ENUM
    if is_admin_path:
        log.info("Token pathway: GRID_ADMIN (Enterprise Grid + admin scopes)")
    else:
        log.info("Token pathway: BOT_STANDARD")

    log.info("")
    log.info("Stage 1/3: Loading user directory…")
    fetcher.load_user_cache()
    log.info("  %d users cached", len(fetcher.user_cache))

    log.info("")
    log.info("Stage 2/3: Discovering channels…")
    if is_admin_path:
        admin_client = SlackAdminClient(token=fetcher.token, base_url=fetcher.base_url)
        channels = admin_client.search_channels()
        if not args.include_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        if args.channels:
            only = set(args.channels)
            channels = [c for c in channels if c.get("id") in only or c.get("name") in only]
        if not args.include_dms:
            channels = [c for c in channels if not c.get("is_im") and not c.get("is_mpim")]
    else:
        channel_types = _channel_types(include_private=True, include_dms=args.include_dms)
        channels = fetcher.list_channels(
            types=channel_types,
            exclude_archived=not args.include_archived,
            only_channels=args.channels,
        )
    channels = sorted(channels, key=lambda c: (c.get("name") or "", c.get("id") or ""))

    if args.resume and completed_channels:
        done = set(completed_channels)
        channels = [c for c in channels if c.get("id") not in done]
        log.info("  Resume active: %d channels remaining", len(channels))

    if not channels:
        log.warning("No channels matched selection (channels=%s)", args.channels)
        return

    log.info("  %d channels to process", len(channels))

    # Bot tokens need membership before conversations.history returns content —
    # auto-join public channels the bot isn't in yet. Admin user tokens on
    # Enterprise Grid use admin.conversations.* and don't need to join.
    if not args.no_auto_join and not is_admin_path:
        joined = 0
        skipped_private = 0
        for channel in channels:
            if channel.get("is_member"):
                continue
            if channel.get("is_private"):
                skipped_private += 1
                continue
            if fetcher.join_channel(channel.get("id", "")):
                channel["is_member"] = True
                joined += 1
        if joined:
            log.info("  Auto-joined %d public channel(s) for read access", joined)
        if skipped_private:
            log.warning("  %d private channel(s) require manual invite "
                        "(bot cannot self-join; use an admin user token to avoid this)")

    log.info("")
    log.info("Stage 3/3: Processing channels…")
    processed = 0
    failed = 0
    # Count messages dropped as noise, by reason, so operators can see
    # how much signal the filter removed (previously invisible).
    noise_counter: dict[str, int] = {}

    for channel in channels:
        cid = channel.get("id", "")
        cname = channel.get("name") or cid
        label = f"#{cname}" if cname and not str(cname).startswith("#") else str(cname)

        try:
            events = process_channel(
                fetcher=fetcher,
                channel=channel,
                org=args.org,
                since=since,
                dry_run=args.dry_run,
                seen_ids=seen_ids,
                log=log,
                noise_counter=noise_counter,
            )

            if args.dry_run:
                log.info("  [DRY-RUN] %s (%s): %d events", label, cid, len(events))
            else:
                append_events(events, events_file)
                total_written += len(events)
                completed_channels.append(cid)
                save_checkpoint(checkpoint_path, completed_channels, seen_ids, total_written)
                log.info("  %s (%s): +%d events (total=%d)", label, cid, len(events), total_written)

            processed += 1
        except Exception as exc:
            failed += 1
            log.exception("  Failed channel %s (%s): %s", label, cid, exc)

    if args.mode == "hybrid" and socket_ingestor is not None:
        log.info("")
        log.info("Starting Socket Mode listener after polling backfill…")
        socket_ingestor.run()
        return

    elapsed = time.time() - pipeline_start
    events_written = total_written if not args.dry_run else 0
    noise_total = sum(noise_counter.values())
    noise_breakdown = ",".join(f"{k}={v}" for k, v in sorted(noise_counter.items())) or "none"
    log.info("")
    log.info("═" * 55)
    log.info("Slack ingestion complete")
    log.info("  Channels processed : %d", processed)
    log.info("  Channels failed    : %d", failed)
    log.info("  Events written     : %d", events_written)
    log.info("  Messages dropped   : %d (%s)", noise_total, noise_breakdown)
    if not args.dry_run:
        log.info("  Output file        : %s", events_file)
        log.info("  Checkpoint         : %s", checkpoint_path)
    log.info("  Duration           : %.1fs", elapsed)
    log.info("═" * 55)

    log_summary(
        "slack_fetch",
        org=args.org,
        channels_processed=processed,
        channels_failed=failed,
        events_written=events_written,
        noise_dropped=noise_total,
        noise_breakdown=noise_breakdown,
        duration_s=round(elapsed, 1),
        output=str(events_file) if not args.dry_run else None,
    )
    # A 0-event polling run is almost always an upstream auth/scope bug;
    # fail loudly here rather than letting an empty artifact cascade.
    check_nonempty(
        "slack_fetch",
        count=events_written,
        what="events written",
        allow_empty=args.allow_empty or args.dry_run,
    )


if __name__ == "__main__":
    main()
