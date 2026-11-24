"""
core/exceptions.py
──────────────────
Typed exception hierarchy for the entire backend.

Every exception inherits from LoreError so callers can catch all
lore-specific errors with a single except clause, or target a specific
subclass for fine-grained handling.

api/errors.py maps these to HTTP status codes.
"""


class LoreError(Exception):
    """Base exception for all Lore backend errors."""


# ── Configuration ──────────────────────────────────────────────────────

class ConfigError(LoreError):
    """Invalid or missing configuration (e.g. unknown LLM provider)."""


# ── Authentication & authorisation ─────────────────────────────────────

class AuthError(LoreError):
    """Authentication or authorisation failure."""


class TokenExpiredError(AuthError):
    """JWT token has expired."""


class TokenInvalidError(AuthError):
    """JWT token is malformed or the signature is invalid."""


# ── Resources ──────────────────────────────────────────────────────────

class NotFoundError(LoreError):
    """Requested resource does not exist."""


class ThreadNotFoundError(NotFoundError):
    """Conversation thread does not exist or has been deleted."""


class TurnNotFoundError(NotFoundError):
    """Turn does not exist in the given thread."""


class PermissionDeniedError(LoreError):
    """User attempted to access a resource owned by another user."""


# ── External services ─────────────────────────────────────────────────

class GraphUnavailableError(LoreError):
    """Neo4j graph database is unavailable.

    The query pipeline degrades gracefully when this is raised:
    vector-only results are returned instead of empty.
    """


class LLMError(LoreError):
    """LLM provider call failed (timeout, rate limit, bad response)."""


class EmbeddingError(LLMError):
    """Embedding call failed. Queries cannot proceed without embeddings."""


class RerankError(LoreError):
    """Reranker provider failed. Callers degrade to the pre-rerank order
    (hybrid graph+BM25 score) rather than failing the query."""


class PostgresError(LoreError):
    """PostgreSQL operation failed."""
