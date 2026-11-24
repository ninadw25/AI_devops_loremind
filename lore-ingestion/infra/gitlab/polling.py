"""
infra/gitlab/polling.py
───────────────────────
APScheduler-backed polling for all configured GitLab projects.
Replaces chronograph/gitlab_polling.py with a plugin-aware version.

Reads project list from:
    GITLAB_PROJECTS="org/repo:12345,other/repo:67890"   (multi)
    GITLAB_PROJECT_NAME + GITLAB_PROJECT_ID              (single, legacy)
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
from infra.gitlab.fetcher import GitLabPlugin
from health import heartbeat

log = logging.getLogger("infra.gitlab.polling")

_scheduler: Optional[BackgroundScheduler] = None


def _parse_projects(settings) -> list[dict]:
    """Parse GITLAB_PROJECTS env var into list of {project_id, gitlab_id}."""
    multi = os.environ.get("GITLAB_PROJECTS", "").strip()
    if multi:
        projects = []
        for entry in multi.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                log.warning("[gitlab poll] Skipping malformed GITLAB_PROJECTS entry: %r", entry)
                continue
            name, gid = entry.rsplit(":", 1)
            projects.append({"project_id": name.strip(), "gitlab_id": gid.strip()})
        return projects

    # Legacy single-project env vars
    pid = os.environ.get("GITLAB_PROJECT_ID", "")
    pname = os.environ.get("GITLAB_PROJECT_NAME", "")
    if pid and pname:
        return [{"project_id": pname, "gitlab_id": pid}]

    log.warning(
        "[gitlab poll] No GitLab projects configured. "
        "Set GITLAB_PROJECTS=\"org/repo:12345\" or GITLAB_PROJECT_NAME + GITLAB_PROJECT_ID."
    )
    return []


def _run_all_projects() -> None:
    """Run one poll cycle for every configured GitLab project."""
    heartbeat("gitlab_poller")  # start of cycle: "I'm awake and trying"
    settings = get_settings()
    projects = _parse_projects(settings)
    if not projects:
        return

    store = make_store(settings)
    if not store.connect():
        log.error("[gitlab poll] Cannot connect to store — aborting cycle")
        return

    try:
        for project in projects:
            plugin = GitLabPlugin(
                base_url=settings.gitlab_base_url,
                pat=settings.gitlab_pat,
                project_id=project["project_id"],
                gitlab_id=project["gitlab_id"],
            )
            asyncio.run(run_cycle(plugin, store))
    finally:
        store.disconnect()


def start_polling_scheduler() -> None:
    """Configure and start the background scheduler for GitLab polling."""
    global _scheduler
    if _scheduler and _scheduler.running:
        log.warning("[gitlab poll] Scheduler already running")
        return

    settings = get_settings()
    interval = settings.gitlab_poll_interval_seconds

    if not settings.gitlab_pat:
        log.info("[gitlab poll] GITLAB_PAT not set — GitLab polling disabled")
        return

    if interval <= 0:
        log.warning("[gitlab poll] Polling disabled (interval=%ds)", interval)
        return

    log.info("[gitlab poll] Starting scheduler — every %ds", interval)
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _run_all_projects,
        "interval",
        seconds=interval,
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
        id="gitlab_poll_cycle",
        max_instances=1,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    log.info("[gitlab poll] Scheduler started")
