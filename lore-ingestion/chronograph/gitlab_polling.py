"""
chronograph/gitlab_polling.py — DEPRECATED
──────────────────────────────────────────
Kept for backward compatibility. New code: use infra.gitlab.polling directly.
The start_polling_scheduler() function now delegates to the plugin-aware poller.
"""

import logging

log = logging.getLogger(__name__)


def start_polling_scheduler():
    """Start GitLab polling. Delegates to infra.gitlab.polling."""
    from infra.gitlab.polling import start_polling_scheduler as _start
    _start()


async def run_poll_cycle():
    """Run one GitLab poll cycle. Delegates to infra.gitlab.polling."""
    from infra.gitlab.polling import _run_all_projects
    _run_all_projects()
