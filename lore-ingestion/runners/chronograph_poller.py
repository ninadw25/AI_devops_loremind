"""
runners/chronograph_poller.py
──────────────────────────────
Standalone poller process for code-intelligence ingestion.
Starts schedulers for all configured code-host sources and blocks.

Run:
    python -m runners.chronograph_poller

Container entrypoint for the chronograph-poller Docker service.
"""

import logging
import signal
import sys
import time

from common.logger import setup_logging
from infra.gitlab.polling import start_polling_scheduler as start_gitlab
from infra.github.polling import start_polling_scheduler as start_github
from health.server import start_health_server

log = logging.getLogger("runners.chronograph_poller")


def _shutdown(sig, frame):
    log.info("Received signal %s — shutting down", sig)
    sys.exit(0)


def main():
    setup_logging(org="chronograph", source="poller")
    log.info("Chronograph poller starting")

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    start_gitlab()
    start_github()
    start_health_server()  # zero-dep shim: /health + /health/deep, same process as pollers

    log.info("All schedulers started — polling indefinitely")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
