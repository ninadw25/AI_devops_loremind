"""
Store package. Import make_store to get the Postgres store.

    from chronograph.store import make_store
    store = make_store()
    store.connect()

Backend: PostgreSQL + pgvector only. The DSN is read from DATABASE_URL,
falling back to POSTGRES_URI.
"""

from __future__ import annotations

import os
import logging

log = logging.getLogger("chronograph.store")


def make_store(settings=None):
    """Return the PgStore instance. Caller must call .connect()."""
    from common.config import get_settings
    from chronograph.store.pg_store import PgStore

    s = settings or get_settings()
    dsn = (
        os.environ.get("DATABASE_URL", "")
        or getattr(s, "database_url", "")
        or getattr(s, "postgres_uri", "")
    )
    if not dsn:
        raise RuntimeError(
            "Chronograph store requires PostgreSQL: set DATABASE_URL or POSTGRES_URI."
        )
    log.info("[store] Using PgStore (PostgreSQL + pgvector)")
    return PgStore(dsn)
