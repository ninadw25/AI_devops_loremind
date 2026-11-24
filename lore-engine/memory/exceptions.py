"""
memory/exceptions.py
────────────────────
Memory-layer exceptions. All inherit from core.LoreError so the
api/errors.py handler can catch and map them to HTTP codes.
"""

from core.exceptions import LoreError, NotFoundError, PermissionDeniedError  # noqa: F401


class ThreadNotFoundError(NotFoundError):
    """Raised when a thread does not exist or has been deleted."""


class TurnNotFoundError(NotFoundError):
    """Raised when a turn does not exist."""


# PermissionDeniedError is re-exported from core so callers can import
# it from either core.exceptions or memory.exceptions.
__all__ = ["ThreadNotFoundError", "TurnNotFoundError", "PermissionDeniedError"]
