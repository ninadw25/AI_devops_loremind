"""
Store package. Import make_store to get the Postgres store.

    from chronograph.store import make_store
    store = make_store()
    store.connect()

Backend: PostgreSQL + pgvector only. The DSN comes from the lore-engine
Settings.chronograph_dsn property (CHRONOGRAPH_DSN env, else MEMORY_POSTGRES_URI).
This is the query half of chronograph, relocated into lore-engine; ingestion
keeps the fetch/parse/embed half.
"""

from __future__ import annotations

import logging

log = logging.getLogger("chronograph.store")


def make_store(settings=None):
    """Return the PgStore instance. Caller must call .connect().

    DSN is read from the engine Settings.chronograph_dsn (falls back to the
    conversation-memory Postgres). No dependency on the ingestion common.config.
    """
    from core.settings import get_settings
    from chronograph.store.pg_store import PgStore

    s = settings or get_settings()
    dsn = getattr(s, "chronograph_dsn", "") or ""
    if not dsn:
        raise RuntimeError(
            "Chronograph store requires a DSN: set CHRONOGRAPH_DSN or MEMORY_POSTGRES_URI."
        )
    log.info("[store] Using PgStore (PostgreSQL + pgvector)")
    return PgStore(dsn)
