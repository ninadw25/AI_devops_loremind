"""
api/auth.py
───────────
The gateway trust boundary — the only module that knows about tokens.

lore-engine has exactly ONE way in: a request forwarded by the elysia
core-backend gateway, carrying the shared internal secret. There is no password
login, no direct-access token path, and no unauthenticated mode. The legacy
HS256 login (AUTH_USERS / AUTH_SALT / JWT_SECRET_KEY) was removed 2026-07-22 —
it was a second, weaker door into the same data, mounted publicly.

Raises typed errors instead of returning None:
  TokenInvalidError  — malformed token
  AuthError          — failed service-trust check
"""

from __future__ import annotations

import hmac
from typing import Optional

import jwt
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.exceptions import AuthError, TokenInvalidError
from core.logging import get_logger, set_correlation
from core.settings import get_settings
from core.types import UserContext

log = get_logger(__name__)
security = HTTPBearer(auto_error=False)


# ── Service-to-service (gateway) trust ────────────────────────────────
#
# lore-engine runs behind the elysia core-backend gateway. The gateway has
# already verified the user's EdDSA JWT and computed content scopes; it then
# calls lore-engine with the shared internal secret in X-Internal-Auth and the
# identity + scopes injected into the request body. This dependency is the
# trust boundary: it authenticates that a request really came from the gateway,
# so the body-injected scopes cannot be forged by a direct caller.
#
# See docs/INTEGRATION_ELYSIA.md.

async def require_gateway(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_internal_auth: Optional[str] = Header(default=None, alias="X-Internal-Auth"),
    x_account_id: Optional[str] = Header(default=None, alias="X-Account-Id"),
    # Injected by FastAPI from the annotation; defaulted so unit tests can call
    # this dependency directly without building a Request.
    request: Request = None,
) -> UserContext:
    """Trust boundary for gateway-forwarded requests. Returns the caller's
    identity (UserContext) with allowed_scopes left empty — content scopes are
    delivered in the request body (the locked Plane-B contract), not here.

    1. Service trust: constant-time check of the shared internal secret.
       FAIL-CLOSED with no bypass — an unset secret refuses every request with
       503. There is deliberately no dev flag and no environment exemption.
    2. Identity: decoded from the gateway-forwarded JWT WITHOUT re-verifying its
       signature — the gateway (elysia core-backend) already verified it via
       JWKS, and the internal secret + network isolation are the real trust
       boundary. This keeps lore-engine free of JWKS/EdDSA verification and
       needs no extra identity injection from the gateway.

    Because step 2 does not check the signature, step 1 is the ONLY thing
    separating a direct caller from every tenant's knowledge graph. That is why
    an unset secret cannot mean "allow": a forged, unsigned token would sail
    straight through. It used to, and this is the fix.
    """
    secret = get_settings().internal_auth_secret
    if not secret:
        # Server misconfiguration, not a client failure — no credential the
        # caller could present would help, so 503 rather than 401.
        log.error(
            "internal_auth_unconfigured",
            note="INTERNAL_AUTH_SECRET unset — refusing all gateway requests",
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "lore-engine is not configured: INTERNAL_AUTH_SECRET is unset. "
                "Set it to the gateway's shared secret."
            ),
        )
    if not hmac.compare_digest(x_internal_auth or "", secret):
        raise AuthError("Invalid or missing internal service credentials")

    if not credentials:
        raise AuthError("No credentials provided")
    try:
        # Signature already verified by the gateway (JWKS from elysia-rbac), and
        # this endpoint is reachable only with the shared internal secret checked
        # above — so the engine decodes for claims rather than re-verifying.
        #
        # Expiry is still enforced: PyJWT disables *all* checks when
        # verify_signature is off, which would let a leaked-but-expired token
        # through if the gateway ever forwarded one. Signature trust is
        # delegated; freshness is not.
        payload = jwt.decode(
            credentials.credentials,
            options={"verify_signature": False, "verify_exp": True},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalidError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalidError(f"Malformed token: {exc}") from exc

    user_id = payload.get("sub") or "unknown"
    set_correlation(user_id=user_id)
    # Rate-limit key: every request arrives from the gateway, so the client IP
    # is the same for all tenants (api/main.py:_rate_limit_key).
    if request is not None:
        request.state.user_id = user_id
    return UserContext(
        user_id=user_id,
        org=payload.get("org", "") or "",
        # No default: rbac issues the membership role claim; a token without
        # one gets [] access groups downstream (domain/rbac.py fail-closes).
        role=payload.get("role") or "",
        allowed_scopes=[],                       # scopes arrive in the body
        jira_account_id=x_account_id or payload.get("account_id") or None,
    )
