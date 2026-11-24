"""
control/auth.py
───────────────
Service-to-service auth for the control API: a constant-time check of the
`X-Internal-Auth` header against `control_internal_secret`. The control plane
(elysia-rbac / elysia-backend) is the only caller; end users never reach this
server directly.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException

from common.config import get_settings
from common.logger import get_logger

log = get_logger("control.auth")


async def require_internal(
    x_internal_auth: Optional[str] = Header(default=None, alias="X-Internal-Auth"),
) -> None:
    """FastAPI dependency. Raises unless the shared secret matches.

    Fail-closed on an unset secret. This endpoint starts full ingestion runs with
    a caller-supplied service-account key, so "no secret configured" must mean
    "serve nobody", not "serve everybody" — the permissive-dev variant made an
    unconfigured deploy silently world-writable. 503, not 401, because the fault
    is the server's missing config and no client credential can fix it.
    """
    secret = get_settings().control_internal_secret
    if not secret:
        log.error(
            "control_internal_auth_unconfigured — CONTROL_INTERNAL_SECRET unset; "
            "refusing all requests",
        )
        raise HTTPException(
            status_code=503,
            detail="Control API is not configured: CONTROL_INTERNAL_SECRET is unset",
        )
    if not hmac.compare_digest(x_internal_auth or "", secret):
        raise HTTPException(status_code=401, detail="Invalid internal credentials")
