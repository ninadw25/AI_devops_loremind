"""
infra/tenant_registry.py
────────────────────────
Per-org (multi-tenant) connection routing for the hybrid deployment
(docs/HYBRID_DEPLOYMENT.md §3).

One shared lore-engine serves many clients whose data stores (Neo4j knowledge
graph + Postgres conversation memory) each live in their own VPC. This registry
resolves an org slug to that client's endpoints by calling elysia-rbac's internal
registry endpoint (`GET /internal/registry/connections/{org}/resolve`), caches a
`Neo4jClient` and a memory `MemoryService` per org for `tenant_cache_ttl_seconds`,
and hands them to the query pipeline.

When tenant routing is disabled (`settings.tenant_routing_enabled is False`) or no
org context is present, `neo4j_for()` / `memory_for()` return the single static
client built from `NEO4J_URI` / `MEMORY_POSTGRES_URI` — preserving the original
single-tenant behaviour for dev / self-host.

Fail-closed: when routing IS enabled for an org, its memory MUST route to that
client's Postgres. If the registry has no `memoryPg` for the org we raise rather
than silently fall back to the shared static DB (which would land one client's
conversations in the wrong VPC).
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from core.exceptions import ConfigError, GraphUnavailableError, PostgresError
from core.logging import get_logger
from core.settings import Settings
from infra.neo4j_client import Neo4jClient

log = get_logger("infra.tenant_registry")


def _pg_dsn_with_password(uri: str, password: str) -> str:
    """Inject the resolved secret into a stored Postgres DSN.

    The registry stores the endpoint (`postgresql://user@host:5432/lore_data`)
    and the password separately (secret ref). Rebuild the userinfo, replacing any
    password already present and URL-encoding both parts.
    """
    parts = urlsplit(uri)
    user = parts.username or "postgres"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{parts.hostname or ''}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass
class _Cached:
    client: Neo4jClient
    uri: str
    expires_at: float


@dataclass
class _CachedMemory:
    service: object          # services.memory_service.MemoryService
    client: object           # infra.postgres_client.PostgresClient
    dsn: str
    expires_at: float


@dataclass
class _CachedChrono:
    store: object            # chronograph.store.pg_store.PgStore
    dsn: str
    expires_at: float


class TenantRegistry:
    """Resolves and caches a per-org Neo4jClient. Thread-safe (sync FastAPI
    dependencies run in a threadpool)."""

    def __init__(
        self,
        settings: Settings,
        fallback_graph: Neo4jClient,
        fallback_memory: object | None = None,
    ) -> None:
        self._settings = settings
        self._fallback = fallback_graph
        self._fallback_memory = fallback_memory
        self._enabled = settings.tenant_routing_enabled
        self._ttl = max(1, settings.tenant_cache_ttl_seconds)
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()
        self._mem_cache: dict[str, _CachedMemory] = {}
        self._mem_lock = asyncio.Lock()
        self._chrono_cache: dict[str, _CachedChrono] = {}
        self._chrono_static: object | None = None
        self._chrono_lock = threading.Lock()

        if self._enabled and not settings.rbac_url:
            # Fail loud at startup rather than on the first query.
            raise ConfigError(
                "TENANT_ROUTING_ENABLED is true but RBAC_URL is not set",
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def neo4j_for(self, org: str) -> Neo4jClient:
        """Return the Neo4jClient for `org`. Falls back to the static client when
        routing is disabled or no org is supplied."""
        if not self._enabled or not org:
            return self._fallback

        with self._lock:
            cached = self._cache.get(org)
            if cached and cached.expires_at > time.monotonic():
                return cached.client

            neo4j = self._resolve(org)["neo4j"]
            uri, password = neo4j["uri"], neo4j["password"]

            # Endpoint unchanged → keep the existing driver, just extend the TTL.
            if cached and cached.uri == uri:
                cached.expires_at = time.monotonic() + self._ttl
                return cached.client

            if cached:  # endpoint changed → replace the driver
                cached.client.close()

            client = Neo4jClient(uri, self._settings.neo4j_username, password)
            self._cache[org] = _Cached(
                client=client,
                uri=uri,
                expires_at=time.monotonic() + self._ttl,
            )
            log.info(
                "tenant_neo4j_resolved",
                org=org,
                uri=uri,
                available=client.available,
            )
            return client

    def _resolve(self, org: str) -> dict:
        """Call elysia-rbac to materialise the org's data-store connections.

        Returns the parsed ResolvedConnection: a required `neo4j` block and an
        optional `memory` block ({uri, password}) mirroring the registry's
        `memoryPg`. Blocking (uses `requests`); the async memory path calls this
        via `asyncio.to_thread`.
        """
        base = self._settings.rbac_url.rstrip("/")
        url = f"{base}/internal/registry/connections/{org}/resolve"
        try:
            resp = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._settings.rbac_internal_secret}",
                },
                timeout=self._settings.rbac_timeout_s,
            )
        except requests.RequestException as exc:
            raise GraphUnavailableError(
                f"registry unreachable for org '{org}': {exc}",
            ) from exc

        if resp.status_code != 200:
            raise GraphUnavailableError(
                f"registry resolve failed for org '{org}': HTTP {resp.status_code}",
            )

        data = resp.json()
        neo4j = data.get("neo4j") or {}
        if not neo4j.get("uri") or neo4j.get("password") is None:
            raise GraphUnavailableError(
                f"registry returned no neo4j connection for org '{org}'",
            )
        return {"neo4j": neo4j, "memory": data.get("memoryPg")}

    # ── Memory (Postgres) routing ─────────────────────────────────────────

    async def memory_for(self, org: str):
        """Return the `MemoryService` for `org`, routed to that client's Postgres.

        Falls back to the static memory service when routing is disabled or no org
        is supplied. When routing IS enabled but the registry has no memory
        connection for the org, fails closed (PostgresError) — never routes a
        client's conversations to the shared static DB.
        """
        if not self._enabled or not org:
            return self._fallback_memory

        async with self._mem_lock:
            cached = self._mem_cache.get(org)
            if cached and cached.expires_at > time.monotonic():
                return cached.service

            memory = (await asyncio.to_thread(self._resolve, org)).get("memory")
            if not memory or not memory.get("uri") or memory.get("password") is None:
                raise PostgresError(
                    f"registry returned no memory connection for org '{org}' "
                    "(tenant routing is enabled; refusing to fall back to the "
                    "shared memory DB)",
                )
            dsn = _pg_dsn_with_password(memory["uri"], memory["password"])

            # Endpoint unchanged → keep the existing pool, just extend the TTL.
            if cached and cached.dsn == dsn:
                cached.expires_at = time.monotonic() + self._ttl
                return cached.service

            if cached:  # endpoint changed → dispose the old pool
                await cached.client.close()

            service, client = await self._build_memory(dsn)
            self._mem_cache[org] = _CachedMemory(
                service=service,
                client=client,
                dsn=dsn,
                expires_at=time.monotonic() + self._ttl,
            )
            log.info("tenant_memory_resolved", org=org, host=urlsplit(dsn).hostname)
            return service

    async def _build_memory(self, dsn: str):
        """Build a fresh MemoryService chain bound to `dsn`. Returns (service,
        client) so the caller can cache and later close the pool."""
        from infra.postgres_client import PostgresClient
        from memory import MemoryConfig, MemoryManager
        from memory.stores.postgres_store import PostgresLongTermStore
        from services.memory_service import MemoryService

        client = PostgresClient(dsn, schema=self._settings.memory_schema)
        await client.connect()
        store = PostgresLongTermStore(client)
        await store.ensure_indexes()
        manager = MemoryManager(
            config=MemoryConfig(recent_turns_in_context=self._settings.memory_recent_turns),
            store=store,
        )
        return MemoryService(manager), client

    def close(self) -> None:
        """Close every cached Neo4j tenant driver. The static fallback is owned by
        lifespan and closed separately."""
        with self._lock:
            for cached in self._cache.values():
                cached.client.close()
            self._cache.clear()

    async def close_memory(self) -> None:
        """Dispose every cached per-org memory pool. The static fallback pool is
        owned by lifespan and closed separately."""
        async with self._mem_lock:
            for cached in self._mem_cache.values():
                await cached.client.close()
            self._mem_cache.clear()

    # ── Chronograph (Postgres code-intelligence store) routing ────────────
    # Chronograph shares each client's `lore_data` Postgres (the `chronograph`
    # schema, alongside `memory`) — so it routes on the SAME registry `memoryPg`
    # connection as conversation memory. Sync (PgStore uses psycopg2), mirroring
    # neo4j_for.

    def chronograph_for(self, org: str):
        """Return a connected chronograph `PgStore` for `org`, routed to that
        client's Postgres. Falls back to the single static store when routing is
        disabled or no org is supplied. When routing IS enabled but the registry
        has no connection for the org, fails closed (PostgresError) — never serves
        another tenant's code intelligence from the shared static DB."""
        if not self._enabled or not org:
            return self._static_chrono_store()

        with self._chrono_lock:
            cached = self._chrono_cache.get(org)
            if cached and cached.expires_at > time.monotonic():
                return cached.store

            memory = self._resolve(org).get("memory")
            if not memory or not memory.get("uri") or memory.get("password") is None:
                raise PostgresError(
                    f"registry returned no connection for org '{org}' (tenant "
                    "routing is enabled; refusing to serve chronograph from the "
                    "shared store)",
                )
            dsn = _pg_dsn_with_password(memory["uri"], memory["password"])

            # Endpoint unchanged → keep the existing store, just extend the TTL.
            if cached and cached.dsn == dsn:
                cached.expires_at = time.monotonic() + self._ttl
                return cached.store

            if cached:  # endpoint changed → dispose the old store
                cached.store.disconnect()

            store = self._build_chrono_store(dsn)
            self._chrono_cache[org] = _CachedChrono(
                store=store,
                dsn=dsn,
                expires_at=time.monotonic() + self._ttl,
            )
            log.info("tenant_chronograph_resolved", org=org, host=urlsplit(dsn).hostname)
            return store

    def _static_chrono_store(self):
        """Build (once) and return the fallback chronograph store from
        `CHRONOGRAPH_DSN`/`MEMORY_POSTGRES_URI`. Used when routing is disabled —
        preserves single-tenant/dev behaviour."""
        with self._chrono_lock:
            if self._chrono_static is None:
                self._chrono_static = self._build_chrono_store(
                    self._settings.chronograph_dsn,
                )
            return self._chrono_static

    def _build_chrono_store(self, dsn: str):
        from chronograph.store.pg_store import PgStore

        store = PgStore(dsn)
        if not store.connect():
            raise PostgresError("chronograph store connection failed")
        return store

    def close_chronograph(self) -> None:
        """Disconnect every cached per-org chronograph store plus the static one."""
        with self._chrono_lock:
            for cached in self._chrono_cache.values():
                cached.store.disconnect()
            self._chrono_cache.clear()
            if self._chrono_static is not None:
                self._chrono_static.disconnect()
                self._chrono_static = None
