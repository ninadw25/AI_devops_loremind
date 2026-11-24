"""
chronograph/store/store_factory.py
───────────────────────────────────
Factory that returns the Store backend. Chronograph is PostgreSQL + pgvector only.

DSN is read from DATABASE_URL, falling back to POSTGRES_URI.

Usage:
    from chronograph.store.store_factory import make_store
    from common.config import get_settings

    store = make_store(get_settings())
    store.connect()
"""

from __future__ import annotations

import os
import logging

log = logging.getLogger("chronograph.store_factory")


def make_store(settings=None):
    """
    Return a PgStore implementation. Caller must call .connect().

    Parameters
    ----------
    settings : Settings | None
        Pass a Settings instance to avoid a second singleton call.
        Defaults to get_settings().
    """
    from common.config import get_settings
    from chronograph.store.pg_store import PgStore

    s = settings or get_settings()
    dsn = (
        os.environ.get("DATABASE_URL", "")
        or getattr(s, "database_url", "")
        or getattr(s, "postgres_uri", "")
    )
    if not dsn:
        raise ValueError(
            "Chronograph store requires PostgreSQL: set DATABASE_URL or POSTGRES_URI."
        )
    log.info("[store_factory] Using PgStore (PostgreSQL + pgvector)")
    return PgStore(dsn)
