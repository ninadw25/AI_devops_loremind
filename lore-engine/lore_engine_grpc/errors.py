"""
lore_engine_grpc.errors — maps core.LoreError subclasses to gRPC status codes.

Mirrors api/errors.py (the HTTP mapping) so a given failure surfaces with the
same semantics on both transports. One place owns the error→gRPC mapping.
"""
from __future__ import annotations

import grpc

from core.exceptions import (
    AuthError,
    ConfigError,
    EmbeddingError,
    GraphUnavailableError,
    LLMError,
    LoreError,
    NotFoundError,
    PermissionDeniedError,
    PostgresError,
    ThreadNotFoundError,
)

# Order matters only for readability; the lookup below is isinstance-based and
# honours subclass relationships (ThreadNotFoundError is a NotFoundError).
_STATUS_MAP: dict[type, grpc.StatusCode] = {
    AuthError:             grpc.StatusCode.UNAUTHENTICATED,
    PermissionDeniedError: grpc.StatusCode.PERMISSION_DENIED,
    NotFoundError:         grpc.StatusCode.NOT_FOUND,
    ThreadNotFoundError:   grpc.StatusCode.NOT_FOUND,
    ConfigError:           grpc.StatusCode.INTERNAL,
    LLMError:              grpc.StatusCode.UNAVAILABLE,
    EmbeddingError:        grpc.StatusCode.UNAVAILABLE,
    GraphUnavailableError: grpc.StatusCode.UNAVAILABLE,
    PostgresError:         grpc.StatusCode.UNAVAILABLE,
}


def status_for(exc: BaseException) -> grpc.StatusCode:
    """Return the gRPC status code for a LoreError (UNKNOWN for anything else)."""
    if isinstance(exc, LoreError):
        for exc_type, code in _STATUS_MAP.items():
            if isinstance(exc, exc_type):
                return code
    return grpc.StatusCode.UNKNOWN


def details_for(exc: BaseException) -> str:
    """Stable, non-leaky detail string: type name + message (no stack trace)."""
    return f"{type(exc).__name__}: {exc}"


async def abort_for(exc: BaseException, context) -> None:
    """Abort the current RPC with the mapped status for ``exc``.

    grpc.aio's ``ServicerContext.abort`` is a coroutine and must be awaited —
    it raises ``AbortError`` and never returns.
    """
    await context.abort(status_for(exc), details_for(exc))


async def abort_thread_lookup(exc: BaseException, context) -> None:
    """Abort a thread-lookup failure while masking thread existence.

    The HTTP path deliberately collapses BOTH ``ThreadNotFoundError`` and
    ``PermissionDeniedError`` to HTTP 404 "Thread not found"
    (``api/routes/query.py`` for build_context, ``api/routes/threads.py`` for
    get_turns/delete_thread) so a caller cannot enumerate or probe thread ids
    by distinguishing 404 (no such thread) from 403 (exists but not yours).
    The gRPC path must match: surface NOT_FOUND for both, and use a generic
    detail string (no exception type/message, which could itself leak).

    Any other ``LoreError`` falls through to its normal status mapping.
    """
    if isinstance(exc, (ThreadNotFoundError, PermissionDeniedError)):
        await context.abort(grpc.StatusCode.NOT_FOUND, "Thread not found")
    await abort_for(exc, context)
