"""
core/logging.py
───────────────
Structured JSON logger with per-request correlation context.

Every log line emitted anywhere in the app automatically includes
{request_id, user_id, thread_id} from the current contextvars state.
This makes it trivial to grep all log lines for a single request.

Usage:
    from core.logging import get_logger, set_correlation, configure_logging

    # Once at app startup (api/main.py):
    configure_logging()

    # In middleware, after assigning request_id:
    set_correlation(request_id="req-abc123", user_id="alice")

    # In any module:
    log = get_logger(__name__)
    log.info("query_complete", confidence="high", latency_ms=412)
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar, Token
from typing import Any

# ── Correlation context ───────────────────────────────────────────────

_correlation: ContextVar[dict[str, str]] = ContextVar(
    "lore_correlation", default={}
)


def set_correlation(**fields: str) -> None:
    """Add or update correlation fields in the current async context."""
    ctx = dict(_correlation.get())
    ctx.update({k: v for k, v in fields.items() if v})
    _correlation.set(ctx)


def get_correlation() -> dict[str, str]:
    return _correlation.get()


def clear_correlation() -> None:
    _correlation.set({})


# ── Formatter ─────────────────────────────────────────────────────────

_STDLIB_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", [], None).__dict__)
_SKIP_ATTRS = _STDLIB_ATTRS | {"request_id", "user_id", "thread_id", "message", "asctime"}


class JSONFormatter(logging.Formatter):
    """Emit one JSON object per log line.

    Standard fields: ts, level, logger, msg
    Correlation fields: request_id, user_id, thread_id (from contextvars)
    Extra fields: any keyword arguments passed via logging's `extra=`
    """

    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        payload: dict[str, Any] = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "user_id": getattr(record, "user_id", "-"),
            "thread_id": getattr(record, "thread_id", "-"),
        }

        if record.exc_text:
            payload["exc"] = record.exc_text

        # Merge extra fields attached via logger.info(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in _SKIP_ATTRS and not key.startswith("_"):
                payload[key] = val

        return json.dumps(payload, default=str, ensure_ascii=False)


# ── Filter ────────────────────────────────────────────────────────────

class CorrelationFilter(logging.Filter):
    """Inject correlation fields from the contextvar into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _correlation.get()
        record.request_id = ctx.get("request_id", "-")
        record.user_id = ctx.get("user_id", "-")
        record.thread_id = ctx.get("thread_id", "-")
        return True


# ── Public API ────────────────────────────────────────────────────────

def configure_logging(level: int = logging.INFO) -> None:
    """Wire JSON formatter + correlation filter onto the root logger.

    Call once at application startup (api/main.py lifespan).
    All loggers — including third-party ones — will emit structured JSON.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    handler.addFilter(CorrelationFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


class StructuredLogger:
    """Wraps stdlib Logger to accept kwargs as structured fields.

    Allows: log.info("event", key=val) instead of log.info("event", extra={"key": val})
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._log = logger

    def _emit(self, level: int, msg: str, exc_info: bool = False, **kwargs: Any) -> None:
        if self._log.isEnabledFor(level):
            self._log.log(level, msg, exc_info=exc_info, extra=kwargs or None)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, exc_info=True, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.CRITICAL, msg, **kwargs)


def get_logger(name: str) -> StructuredLogger:
    """Return a structured logger. configure_logging() must be called first."""
    return StructuredLogger(logging.getLogger(name))
