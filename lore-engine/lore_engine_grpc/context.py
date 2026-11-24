"""
lore_engine_grpc.context — build the engine's UserContext from gRPC metadata.

Identity (user_id / org / role / account_id) is supplied by the gateway in call
metadata, NOT in the request message and NOT by forwarding/decoding the user's
JWT. The trust model is unchanged from the HTTP path: the gateway has already
verified the caller's EdDSA JWT (JWKS) and computed their identity + scopes;
the internal-secret check (see interceptors.py) is what makes the engine trust
the metadata. Stating identity in metadata per-call is simply the explicit,
typed equivalent of the HTTP path decoding the forwarded JWT for claims.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.logging import set_correlation
from core.types import UserContext


def metadata_value(context: Any, key: str, default: str = "") -> str:
    """Case-insensitive lookup of a single metadata value by header name."""
    target = key.lower()
    for name, value in (context.invocation_metadata() or ()):
        if name.lower() == target:
            return value
    return default


def user_context_from_metadata(context: Any) -> UserContext:
    """Construct a UserContext from gateway-supplied call metadata.

    ``allowed_scopes`` is intentionally empty here: content scopes are a
    separate concern (see ``ScopeContext.from_metadata``) carried in their own
    metadata keys, not folded into identity.
    """
    account_id = metadata_value(context, "x-account-id") or None
    return UserContext(
        user_id=metadata_value(context, "x-user-id") or "unknown",
        org=metadata_value(context, "x-org"),
        role=metadata_value(context, "x-role") or "member",
        allowed_scopes=[],
        jira_account_id=account_id,
    )


def _json_string_list(context: Any, key: str) -> list[str]:
    raw = metadata_value(context, key)
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(v) for v in parsed]


@dataclass(frozen=True)
class ScopeContext:
    """The caller's content-scope set, read from gRPC call metadata.

    Scopes are NOT a field on any request message (see query.proto) — they
    travel in metadata (x-scope-ids / x-scope-all-ids / x-super-admin, each
    JSON-array- or bool-string-encoded), populated exclusively by the gateway
    after it authenticates the caller. This makes "no scope fields on the
    client-facing request" a structural fact of the schema, not a convention.
    """

    scope_ids: list[str]
    scope_all_ids: list[str]
    super_admin: bool

    @property
    def allowed_scopes(self) -> list[str]:
        """Union of scope_ids + scope_all_ids, deduped, order-preserving."""
        return list(dict.fromkeys([*self.scope_ids, *self.scope_all_ids]))

    @classmethod
    def from_metadata(cls, context: Any) -> "ScopeContext":
        return cls(
            scope_ids=_json_string_list(context, "x-scope-ids"),
            scope_all_ids=_json_string_list(context, "x-scope-all-ids"),
            super_admin=metadata_value(context, "x-super-admin") == "true",
        )


def seed_request_correlation(context: Any, *, thread_id: str | None = None) -> str:
    """Authoritatively seed the logging contextvar from call metadata.

    gRPC server interceptors in grpc.aio do not reliably propagate contextvars
    into the handler task, so each servicer method seeds correlation itself.
    Returns the request id used (the gateway's ``x-request-id`` when present,
    else a generated one for dev) so callers can log it.
    """
    import uuid

    request_id = metadata_value(context, "x-request-id") or uuid.uuid4().hex[:8]
    fields = {
        "request_id": request_id,
        "user_id": metadata_value(context, "x-user-id"),
    }
    if thread_id:
        fields["thread_id"] = thread_id
    set_correlation(**fields)
    return request_id
