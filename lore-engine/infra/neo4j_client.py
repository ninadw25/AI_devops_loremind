"""
infra/neo4j_client.py
─────────────────────
Neo4j driver lifecycle — connection, query execution, graceful degradation.

This module is intentionally thin: it owns the driver and one method.
All Cypher templates live in domain/retrieval/ next to the logic that uses them.

Graceful degradation: if Neo4j is unavailable, `available` is False and
`run()` returns None. Domain functions check `client.available` before
calling run(), so the query pipeline continues with vector-only results.
"""

from __future__ import annotations

import logging
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)


class Neo4jClient:
    """Thin wrapper around the Neo4j Python driver.

    One instance per process, built once in api/main.py lifespan and
    injected via FastAPI Depends. Thread-safe at the driver level.
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri      = uri
        self._user     = user
        self._password = password
        self._driver   = None
        self._available = False
        self._connect()

    def _connect(self) -> None:
        if not self._uri:
            log.info("NEO4J_URI not set — graph queries disabled")
            return
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
            self._driver.verify_connectivity()
            self._available = True
            log.info("neo4j_connected", uri=self._uri)
        except Exception as exc:
            log.warning("neo4j_unavailable", error=str(exc))

    @property
    def available(self) -> bool:
        return self._available

    def run(self, cypher: str, **params: Any) -> list[dict] | None:
        """Execute a Cypher query and return a list of record dicts.

        Returns None if the database is unavailable or the query errors,
        so callers can distinguish 'unavailable' from 'empty result'.
        """
        if not self._available or not self._driver:
            return None
        try:
            with self._driver.session() as session:
                result = session.run(cypher, **params)
                return [dict(r) for r in result]
        except Exception as exc:
            log.warning("cypher_error", error=str(exc), query=cypher[:120])
            return None

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            log.info("neo4j_closed")
