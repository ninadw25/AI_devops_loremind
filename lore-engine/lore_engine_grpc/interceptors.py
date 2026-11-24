"""
lore_engine_grpc.interceptors — per-RPC auth + request-id correlation.

AuthInterceptor is the trust boundary for the gRPC path. Unlike the HTTP
``require_gateway`` dependency — which silently permits requests when
``INTERNAL_AUTH_SECRET`` is unset (a dev/transition affordance) — the gRPC
path is FAIL-CLOSED: it refuses to serve if the secret is not configured or
does not match the gateway-supplied ``x-internal-auth`` metadata. This is an
explicit hardening called out in LOR-111: the engine must never trust
gateway-injected identity/scopes without a verified internal secret.

Correlation is folded into the same interceptor: the gateway's ``x-request-id``
metadata is written into the existing logging contextvar, so every engine log
line for the request carries the gateway's request id (cross-service log
correlation that the HTTP path lacks today).
"""
from __future__ import annotations

import hmac
import uuid
from typing import Any, Awaitable, Callable

import grpc

from core.logging import set_correlation


def _metadata_dict(handler_call_details: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in (handler_call_details.invocation_metadata or ())}


def _aborting_handler(
    original: grpc.RpcMethodHandler,
    code: grpc.StatusCode,
    details: str,
) -> grpc.RpcMethodHandler:
    """Return a handler of the same streaming shape as ``original`` that aborts.

    grpc.aio needs the replacement handler to match the original's unary/stream
    signature so the framework dispatches it correctly. ``abort`` is awaited
    and raises before any message is produced, so the streaming variants never
    actually yield — the ``yield`` exists only to make them async generators.
    """

    async def _unary_abort(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(code, details)

    async def _stream_abort(request: Any, context: grpc.aio.ServicerContext) -> Any:
        await context.abort(code, details)
        yield  # pragma: no cover — abort raises; keeps this an async generator

    if original.request_streaming and original.response_streaming:
        return grpc.stream_stream_rpc_method_handler(_stream_abort)
    if original.response_streaming:
        return grpc.unary_stream_rpc_method_handler(_stream_abort)
    if original.request_streaming:
        return grpc.stream_unary_rpc_method_handler(_unary_abort)
    return grpc.unary_unary_rpc_method_handler(_unary_abort)


class AuthCorrelationInterceptor(grpc.aio.ServerInterceptor):
    """Authenticate the gateway + seed request-id correlation for every RPC."""

    def __init__(self, secret: str) -> None:
        self._secret = (secret or "").encode()

    async def intercept_service(
        self,
        continuation: Callable[[Any], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        md = _metadata_dict(handler_call_details)

        # Seed the logging contextvar so every log line for this RPC carries
        # the gateway's request id and the caller's user id.
        request_id = md.get("x-request-id") or uuid.uuid4().hex[:8]
        set_correlation(request_id=request_id, user_id=md.get("x-user-id", ""))

        provided = (md.get("x-internal-auth") or "").encode()
        # FAIL CLOSED: empty configured secret OR any mismatch → reject.
        if not self._secret or not hmac.compare_digest(provided, self._secret):
            handler = await continuation(handler_call_details)
            return _aborting_handler(
                handler,
                grpc.StatusCode.UNAUTHENTICATED,
                "invalid or missing internal auth secret",
            )

        return await continuation(handler_call_details)
