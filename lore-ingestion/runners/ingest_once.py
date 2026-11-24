"""
runners/ingest_once.py
───────────────────────
One-shot ingestion runner. Runs a full (or incremental if checkpoint exists)
cycle for a single repository and exits. No scheduler.

Usage:
    # GitHub
    python -m runners.ingest_once --source github --repo isitraghav/my-repo

    # GitLab
    python -m runners.ingest_once --source gitlab --repo org/repo --gitlab-id 12345

    # Force full re-sync (ignore checkpoint)
    python -m runners.ingest_once --source github --repo org/repo --full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from common.config import get_settings
from common.logger import setup_logging
from chronograph.store import make_store
from chronograph import polling_checkpoint as cp

log = logging.getLogger("runners.ingest_once")


def _run_github(store, owner: str, repo: str, full: bool) -> dict:
    from infra.github.fetcher import GitHubPlugin
    settings = get_settings()
    if not settings.github_pat:
        raise RuntimeError("GITHUB_PAT is not set in .env")
    plugin = GitHubPlugin(pat=settings.github_pat, owner=owner, repo=repo)
    if full:
        cp.clear("github", f"{owner}/{repo}")
    from chronograph.poller import run_cycle
    return asyncio.run(run_cycle(plugin, store))


def _run_gitlab(store, project_id: str, gitlab_id: str, full: bool) -> dict:
    from infra.gitlab.fetcher import GitLabPlugin
    settings = get_settings()
    if not settings.gitlab_pat:
        raise RuntimeError("GITLAB_PAT is not set in .env")
    plugin = GitLabPlugin(
        base_url=settings.gitlab_base_url,
        pat=settings.gitlab_pat,
        project_id=project_id,
        gitlab_id=gitlab_id,
    )
    if full:
        cp.clear("gitlab", project_id)
    from chronograph.poller import run_cycle
    return asyncio.run(run_cycle(plugin, store))


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot ingestion run")
    parser.add_argument("--source", required=True, choices=["github", "gitlab"],
                        help="Code-host source")
    parser.add_argument("--repo", required=True,
                        help="GitHub: owner/repo. GitLab: internal project slug.")
    parser.add_argument("--gitlab-id", default="",
                        help="GitLab numeric project ID (required for --source gitlab)")
    parser.add_argument("--full", action="store_true",
                        help="Ignore checkpoint — perform a full re-sync")
    parser.add_argument("--ref", default="",
                        help="Branch/tag ref to ingest (default: repo default branch)")
    args = parser.parse_args()

    setup_logging(org=args.repo.replace("/", "_"), source=f"{args.source}_ingest_once")

    log.info("=== ingest_once START source=%s repo=%s full=%s ===",
             args.source, args.repo, args.full)

    store = make_store()
    if not store.connect():
        log.error("Cannot connect to store — check DATABASE_URL / POSTGRES_URI")
        sys.exit(1)

    try:
        if args.source == "github":
            parts = args.repo.split("/", 1)
            if len(parts) != 2:
                log.error("--repo must be owner/repo for GitHub (got %r)", args.repo)
                sys.exit(1)
            owner, repo = parts
            result = _run_github(store, owner, repo, args.full)
        else:
            if not args.gitlab_id:
                log.error("--gitlab-id is required for --source gitlab")
                sys.exit(1)
            result = _run_gitlab(store, args.repo, args.gitlab_id, args.full)
    finally:
        store.disconnect()

    print("\n=== Ingestion result ===")
    print(json.dumps(result, indent=2, default=str))

    if "error" in result:
        log.error("Ingestion failed: %s", result["error"])
        sys.exit(1)

    log.info("=== ingest_once DONE ===")


if __name__ == "__main__":
    main()
