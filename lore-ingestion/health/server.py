"""Tiny zero-dependency HTTP shim exposing /health and /health/deep.

Uses only the stdlib http.server (lore-ingestion has no web framework, and adding one
to Arnav's active branch would be intrusive). Started by runners/chronograph_poller.py
in a daemon thread, alongside the pollers, in the same process — so /health/deep's poller
checks can read the in-process heartbeats.

This is deliberately minimal: when lore-ingestion grows a real server later, swap this
shim for it and the health/ library underneath is reused unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import health.checks  # noqa: F401 — registers all checks
from health.contract import now_iso
from health.registry import run_all

log = logging.getLogger("health.server")
SERVICE = "lore-ingestion"


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — stdlib naming
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            # Shallow: process is up. No probes — safe for constant polling.
            self._send(200, {"status": "healthy", "service": SERVICE, "timestamp": now_iso()})
        elif path == "/health/deep":
            overall, results = run_all()
            self._send(
                200,
                {
                    "status": overall.value,
                    "service": SERVICE,
                    "checks": {k: v.to_dict() for k, v in results.items()},
                    "timestamp": now_iso(),
                },
            )
        else:
            self._send(404, {"error": "not found", "paths": ["/health", "/health/deep"]})

    def log_message(self, fmt: str, *args) -> None:  # silence default stderr spam
        log.debug("%s - %s", self.address_string(), fmt % args)


def start_health_server(port: int | None = None) -> None:
    """Start the health shim in a daemon thread. Never raises — a bind failure logs and
    continues, so the poller is never taken down by the health endpoint."""
    port = port if port is not None else int(os.getenv("HEALTH_PORT", "8090"))
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    except OSError as e:
        log.error("health shim could not bind port %d: %s — continuing without it", port, e)
        return
    threading.Thread(target=httpd.serve_forever, name="health-server", daemon=True).start()
    log.info("health shim listening on http://0.0.0.0:%d (/health, /health/deep)", port)
