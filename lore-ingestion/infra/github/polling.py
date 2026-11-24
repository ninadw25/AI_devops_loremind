"""
infra/github/polling.py
────────────────────────
APScheduler-backed polling for all configured GitHub repositories.

Reads project list from:
    GITHUB_REPOS="owner/repo,owner2/repo2"

Interval: GITHUB_POLL_INTERVAL_SECONDS (default: same as GitLab interval)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler

from common.config import get_settings
from chronograph.store import make_store
from chronograph.poller import run_cycle
from infra.github.fetcher import GitHubPlugin
from health import heartbeat

log = logging.getLogger("infra.github.polling")

_scheduler: Optional[BackgroundScheduler] = None


def _parse_repos(settings) -> list[dict]:
    """Parse GITHUB_REPOS env var into list of {owner, repo}."""
    raw = os.environ.get("GITHUB_REPOS", "").strip()
    if not raw:
        log.info("[github poll] GITHUB_REPOS not set — GitHub polling disabled")
        return []

    repos = []
    for entry in raw.split(","):
        entry = entry.strip()
        if "/" not in entry:
            log.warning("[github poll] Skipping malformed GITHUB_REPOS entry: %r", entry)
            continue
        owner, repo = entry.split("/", 1)
        repos.append({"owner": owner.strip(), "repo": repo.strip()})
    return repos


def _run_all_repos() -> None:
    """Run one poll cycle for every configured GitHub repo."""
    heartbeat("github_poller")  # start of cycle: "I'm awake and trying"
    settings = get_settings()
    repos = _parse_repos(settings)
    if not repos:
        return

    pat = settings.github_pat
    if not pat:
        log.error("[github poll] GITHUB_PAT not set — skipping GitHub cycle")
        return

    store = make_store(settings)
    if not store.connect():
        log.error("[github poll] Cannot connect to store — aborting cycle")
        return

    try:
        for r in repos:
            plugin = GitHubPlugin(pat=pat, owner=r["owner"], repo=r["repo"])
            asyncio.run(run_cycle(plugin, store))
    finally:
        store.disconnect()


def start_polling_scheduler() -> None:
    """Configure and start the background scheduler for GitHub polling."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("[github poll] Scheduler already running")
        return

    settings = get_settings()
    interval = getattr(settings, "github_poll_interval_seconds", settings.gitlab_poll_interval_seconds)

    if not settings.github_pat:
        log.info("[github poll] GITHUB_PAT not set — GitHub polling disabled")
        return

    repos = _parse_repos(settings)
    if not repos:
        return

    if interval <= 0:
        log.warning("[github poll] Polling disabled (interval=%ds)", interval)
        return

    log.info("[github poll] Starting scheduler — every %ds for %d repo(s)", interval, len(repos))
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _run_all_repos,
        "interval",
        seconds=interval,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        id="github_poll_cycle",
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info("[github poll] Scheduler started")
