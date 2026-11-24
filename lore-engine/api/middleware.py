"""
api/middleware.py
─────────────────
All HTTP middleware in one place.

  CorrelationMiddleware   — assigns request_id per request, injects into logs
  SecurityHeadersMiddleware — sets standard security headers on every response
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.logging import set_correlation


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Assign a request_id to every request and inject it into the log context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())[:8]
        set_correlation(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-Frame-Options"]            = "DENY"
        response.headers["X-XSS-Protection"]           = "1; mode=block"
        response.headers["Strict-Transport-Security"]  = "max-age=31536000"
        response.headers["Cache-Control"]               = "no-store"
        return response
