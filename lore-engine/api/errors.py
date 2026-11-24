"""
api/errors.py
─────────────
Maps core.LoreError subclasses to HTTP status codes.

One place owns the error→HTTP mapping. If a new exception type needs a
specific status code, add it here — routes never set status codes directly.
"""

from fastapi import Request
from fastapi.responses import JSONResponse

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

_STATUS_MAP: dict[type, int] = {
    AuthError:             401,
    PermissionDeniedError: 403,
    NotFoundError:         404,
    ThreadNotFoundError:   404,
    ConfigError:           500,
    LLMError:              503,
    EmbeddingError:        503,
    GraphUnavailableError: 503,
    PostgresError:         503,
}


def lore_error_handler(request: Request, exc: LoreError) -> JSONResponse:
    status = 500
    for exc_type, code in _STATUS_MAP.items():
        if isinstance(exc, exc_type):
            status = code
            break
    return JSONResponse(
        status_code=status,
        content={"detail": str(exc), "type": type(exc).__name__},
    )
