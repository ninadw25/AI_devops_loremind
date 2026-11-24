"""
api/models.py
─────────────
Shared request models for gateway-forwarded routes.

GatewayScopedRequest carries the content scopes the core-backend gateway injects
into the request body (Plane B). Identity (user_id / org) comes from the
forwarded JWT via require_gateway, never from the body. Field names are LOCKED
(see elysia SCOPE_FILTER_HANDOFF / docs/CONTENT_SUPERADMIN.md) and mirror
api/routes/query.py:QueryRequest so every route gates identically.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.types import UserScopes


class GatewayScopedRequest(BaseModel):
    """Base body for routes that read scope-gated content. The gateway (which
    authenticates via the internal secret) injects these; a direct client
    cannot forge them."""

    scope_ids:     list[str] = Field(default_factory=list)
    scope_all_ids: list[str] = Field(default_factory=list)
    super_admin:   bool      = Field(default=False)

    @property
    def allowed_scopes(self) -> list[str]:
        """Union of scope_ids (primary) and scope_all_ids, deduped,
        order-preserving — the caller's full allowed-scope set for the gate."""
        return list(dict.fromkeys([*self.scope_ids, *self.scope_all_ids]))

    def to_user_scopes(self) -> UserScopes:
        """Convert to the domain UserScopes the retrieval layer expects."""
        return UserScopes(allowed=self.allowed_scopes, super_admin=self.super_admin)
