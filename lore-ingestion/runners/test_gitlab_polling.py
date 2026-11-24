"""
Local test runner for the GitLab polling feature.
Launches just the scheduler from PR #7 — no Gmail CLI required.

Run: ../.venv/Scripts/python.exe -m runners.test_gitlab_polling
Stop: Ctrl-C
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# Load .env if present (uses python-dotenv if installed; else manual fallback)
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path)
    except ImportError:
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))

from common.logger import setup_logging, get_logger
from chronograph.gitlab_polling import start_polling_scheduler

setup_logging()
log = get_logger("test_gitlab_polling")

log.info("=" * 60)
log.info("GitLab polling test runner — PR #7 (LOR-38)")
log.info("project: %s (id=%s)", os.environ.get("GITLAB_PROJECT_NAME", "?"), os.environ.get("GITLAB_PROJECT_ID", "?"))
log.info("interval: %ss", os.environ.get("GITLAB_POLL_INTERVAL_SECONDS", "?"))
log.info("postgres: %s", os.environ.get("DATABASE_URL", "?"))
log.info("=" * 60)

start_polling_scheduler()

log.info("Scheduler started. Press Ctrl-C to stop. Polling indefinitely…")
try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    log.info("Stopping test runner.")
