"""
lore_engine_grpc.server — assemble and run the gRPC server.

build_server() wires the servicers + interceptors onto a grpc.aio server but
does not bind/start it, so tests can drive it in-process. serve_grpc() is the
production entry point called from api/main.py's lifespan: it binds, starts,
and returns the running server for graceful shutdown.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc

from core.logging import get_logger
from core.settings import Settings
from lore.engine.v1 import query_pb2_grpc, threads_pb2_grpc
from lore_engine_grpc.interceptors import AuthCorrelationInterceptor
from lore_engine_grpc.resolver import AppStateServiceResolver, ServiceResolver
from lore_engine_grpc.servicers import QueryServicer, ThreadsServicer

log = get_logger(__name__)


def build_server(
    *,
    secret: str,
    resolver: ServiceResolver,
    pipeline_executor: ThreadPoolExecutor,
    max_workers: int = 16,
) -> grpc.aio.Server:
    """Construct the gRPC server with auth/correlation interceptors attached.

    ``pipeline_executor`` is the dedicated pool that runs the engine's *sync*
    query pipeline off the event loop (one slot per concurrent pipeline); the
    server's own executor (also bounded by ``max_workers``) handles grpc's
    non-async bookkeeping.
    """
    server = grpc.aio.server(
        ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lore-grpc-bookkeep"),
        interceptors=(AuthCorrelationInterceptor(secret),),
        options=_channel_options(),
    )
    query_pb2_grpc.add_QueryServiceServicer_to_server(
        QueryServicer(resolver, pipeline_executor), server
    )
    threads_pb2_grpc.add_ThreadsServiceServicer_to_server(
        ThreadsServicer(resolver), server
    )
    return server


def _channel_options() -> list[tuple[str, Any]]:
    """Keepalive so idle/keepalive probes don't silently drop long streams.

    Mirrors the gRPC-recommended server keepalive tuning; harmless if the
    client (ConnectRPC) doesn't enable keepalive on its side.

    ``grpc.max_metadata_size`` is raised above the 8 KiB default because the
    caller's content scopes ride in metadata (x-scope-ids / x-scope-all-ids,
    JSON-encoded). A user with a large scope set would otherwise exceed the
    default and fail where the HTTP body path never did (LOR-111 review).
    256 KiB fits thousands of SHA-256 scope ids with headroom while staying a
    sane abuse ceiling.
    """
    return [
        ("grpc.keepalive_time_ms", 30_000),
        ("grpc.keepalive_timeout_ms", 10_000),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.max_connection_idle_ms", 5 * 60_000),
        ("grpc.max_metadata_size", 256 * 1024),
    ]


async def serve_grpc(app_state: Any, settings: Settings) -> grpc.aio.Server | None:
    """Start the gRPC server bound to settings.grpc_host:grpc_port.

    Returns the running server (for graceful shutdown) or None if gRPC is
    disabled. Reads services from ``app_state`` via AppStateServiceResolver so
    it shares the exact tenant-routed services the FastAPI app serves over HTTP.
    """
    if not settings.grpc_enabled:
        return None

    if not settings.internal_auth_secret:
        # Fail-closed at boot: refuse to start the gRPC listener without a
        # configured secret, rather than serving an unauthenticated port.
        raise RuntimeError(
            "GRPC_ENABLED=true but INTERNAL_AUTH_SECRET is unset — refusing to "
            "start the gRPC server without a fail-closed internal secret."
        )

    resolver = AppStateServiceResolver(app_state)
    pipeline_executor = ThreadPoolExecutor(
        max_workers=settings.grpc_max_workers,
        thread_name_prefix="lore-grpc-pipeline",
    )
    server = build_server(
        secret=settings.internal_auth_secret,
        resolver=resolver,
        pipeline_executor=pipeline_executor,
        max_workers=settings.grpc_max_workers,
    )
    bind = f"{settings.grpc_host}:{settings.grpc_port}"
    # add_insecure_port returns the bound port number, or 0 on failure (e.g.
    # the port is already in use). A 0 return would otherwise let start()
    # appear to succeed while no listener exists — a silent dead server.
    bound_port = server.add_insecure_port(bind)
    if bound_port == 0:
        pipeline_executor.shutdown(wait=False, cancel_futures=True)
        raise RuntimeError(
            f"gRPC server failed to bind {bind} — port unavailable (in use?)."
        )
    await server.start()
    log.info(
        "grpc_listening",
        bind=bind, port=bound_port, max_workers=settings.grpc_max_workers,
    )
    # Stash the executor so lifespan can shut it down alongside the server.
    app_state.grpc_pipeline_executor = pipeline_executor
    return server
