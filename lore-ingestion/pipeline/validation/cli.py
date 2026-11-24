"""
pipeline/validation/cli.py
──────────────────────────
Command-line entry point for Stage 6 (deterministic validation).

Usage:
    python -m pipeline.validator --org ezobooks
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.logger import get_logger, setup_logging
from common.output_paths import KnowledgePaths

from pipeline.validation.run import run_validation

log = get_logger("pipeline.validator")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 6: Validate extracted triples")
    parser.add_argument("--org", required=True,
                        help="Organization name (e.g. ezobooks)")
    parser.add_argument("--data-dir", default="output",
                        help="Output directory root (default: output)")
    parser.add_argument("--source", choices=["gmail", "slack", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"], default="gmail",
                        help="Input source type (accepted for CLI compatibility)")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if 0 valid triples remain.")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(logs_dir), source="validator")

    src = args.source if args.source != "gmail" else None
    paths = KnowledgePaths(org=args.org, data_dir=data_dir, source=src)
    paths.makedirs()
    raw_path       = paths.triples_raw
    # Prefer centralized identity (resolved_registry.json); fall back to the
    # Gmail-built person_registry when no identity run exists.
    from pipeline.identity.registry_view import registry_source
    registry_path  = registry_source(args.org, data_dir)
    validated_path = paths.triples_validated
    rejected_path  = paths.triples_rejected
    log.info("Registry source: %s", registry_path.name)

    if not raw_path.exists():
        log.error("Raw triples not found: %s", raw_path)
        log.error("Run triple_extractor first.")
        return

    if not registry_path.exists():
        log.error("Person registry not found: %s", registry_path)
        return

    run_validation(raw_path, registry_path, validated_path, rejected_path,
                   allow_empty=args.allow_empty)


if __name__ == "__main__":
    main()
