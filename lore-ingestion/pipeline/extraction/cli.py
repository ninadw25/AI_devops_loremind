"""
pipeline/extraction/cli.py
──────────────────────────
Command-line entry point for Stage 5 (LLM triple extraction).

Usage:
    python -m pipeline.triple_extractor --org ezobooks --limit 5
    python -m pipeline.triple_extractor --org ezobooks --resume
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from common.logger import get_logger, setup_logging
from pipeline.extraction.extract import extract_all
from pipeline.input_selection import latest_nonempty_path

log = get_logger("pipeline.triple_extractor")


def main(args):
    data_dir = Path(args.data_dir)
    org = args.org

    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=org, log_dir=str(logs_dir),
                  source="triple_extractor")

    # Select filtered/raw input based on source (gmail, jira, or slack)
    src = getattr(args, "source", None) or "gmail"
    if src == "jira":
        filtered_path = data_dir / "filtered" / f"{org}_jira_filtered.jsonl"
        if not filtered_path.exists():
            log.error("Filtered file not found: %s", filtered_path)
            return
    elif src == "slack":
        raw_dir = data_dir / "raw"
        try:
            filtered_path = latest_nonempty_path(
                str(raw_dir / f"{org}_slack_*.jsonl"),
                label="Slack raw input",
                logger=log,
            )
        except FileNotFoundError as exc:
            log.error(str(exc))
            return
    else:
        filtered_path = data_dir / "filtered" / f"{org}_gmail_filtered.jsonl"
        if not filtered_path.exists():
            raw_dir = data_dir / "raw"
            try:
                filtered_path = latest_nonempty_path(
                    str(raw_dir / f"{org}_slack_*.jsonl"),
                    label="Slack raw fallback input",
                    logger=log,
                )
                log.warning(
                    "Gmail filtered input missing; falling back to Slack raw input: %s",
                    filtered_path,
                )
            except FileNotFoundError:
                log.error("Filtered file not found: %s", filtered_path)
                return

    from common.output_paths import KnowledgePaths
    _kp = KnowledgePaths(org=org, data_dir=data_dir,
                         source=src if src != "gmail" else None)
    _kp.makedirs()
    output_path = _kp.triples_raw
    src_tag = src or "gmail"
    checkpoint_path = (data_dir / "checkpoints"
                       / f"{org}_{src_tag}_triple_extraction_checkpoint.json")
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    if not args.resume and checkpoint_path.exists():
        checkpoint_path.unlink()
        log.info("Cleared previous checkpoint (use --resume to continue)")

    # Load thread IDs if specified
    target_ids = None
    if args.thread_ids:
        with open(args.thread_ids) as f:
            target_ids = set(l.strip() for l in f if l.strip())
        log.info("Targeting %d specific thread IDs from %s",
                 len(target_ids), args.thread_ids)

    asyncio.run(extract_all(
        filtered_path=filtered_path,
        org=org,
        data_dir=data_dir,
        output_path=output_path,
        checkpoint_path=checkpoint_path,
        limit=args.limit,
        offset=args.offset,
        thread_ids=target_ids,
        allow_empty=getattr(args, "allow_empty", False),
        force_restart=getattr(args, "force_restart", False),
    ))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 5: Extract graph triples from email threads")
    parser.add_argument("--org", required=True,
                        help="Organization name (e.g. ezobooks)")
    parser.add_argument("--data-dir", default="output",
                        help="Output directory root (default: output)")
    parser.add_argument("--source", choices=["gmail", "slack", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"], default="gmail",
                        help="Input source type (default: gmail)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Extract only first N threads (for testing)")
    parser.add_argument("--offset", type=int, default=0,
                        help="Skip first N threads (use with --limit)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from checkpoint")
    parser.add_argument("--thread-ids", type=str, default=None,
                        help="File with specific thread IDs to extract (one per line)")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if the output contains 0 threads.")
    parser.add_argument(
        "--force-restart", action="store_true",
        help="If the checkpoint is corrupt, delete it and start over "
             "instead of aborting (default: abort).",
    )
    return parser


def cli_entry():
    parser = build_parser()
    args = parser.parse_args()
    main(args)


if __name__ == "__main__":
    cli_entry()
