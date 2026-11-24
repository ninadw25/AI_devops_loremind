"""
chronograph/store/store_factory.py
───────────────────────────────────
Factory that returns the Store backend. Chronograph is PostgreSQL + pgvector only.

(Engine copy — the query half.) DSN comes from the lore-engine
Settings.chronograph_dsn property; no dependency on the ingestion common.config.

Usage:
    from chronograph.store.store_factory import make_store
    store = make_store()
    store.connect()
"""

from __future__ import annotations

import logging

log = logging.getLogger("chronograph.store_factory")


def make_store(settings=None):
    """Return a PgStore implementation. Caller must call .connect()."""
    from core.settings import get_settings
    from chronograph.store.pg_store import PgStore

    s = settings or get_settings()
    dsn = getattr(s, "chronograph_dsn", "") or ""
    if not dsn:
        raise ValueError(
            "Chronograph store requires PostgreSQL: set CHRONOGRAPH_DSN or MEMORY_POSTGRES_URI."
        )
    log.info("[store_factory] Using PgStore (PostgreSQL + pgvector)")
    return PgStore(dsn)
