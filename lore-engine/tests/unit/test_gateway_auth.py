"""
tests/unit/test_gateway_auth.py
───────────────────────────────
The service-trust boundary (api.auth.require_gateway): internal-secret
enforcement + identity decoded from the gateway-forwarded JWT.
"""

import asyncio
import jwt
import pytest

from core.exceptions import AuthError
from core.settings import _reset_settings, get_settings
from core.types import UserContext


def _creds(token: str):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _token(claims: dict) -> str:
    # Signed with an arbitrary key + alg — require_gateway does NOT verify the
    # signature (the gateway already did), so any decodable JWT works.
    return jwt.encode(claims, "irrelevant", algorithm="HS256")


def _run(coro):
    return asyncio.run(coro)


def _call(token=None, internal=None):
    from api.auth import require_gateway
    creds = _creds(token) if token else None
    return _run(require_gateway(credentials=creds, x_internal_auth=internal, x_account_id=None))


def teardown_function():
    _reset_settings()


def _set_secret(value: str):
    """Pin the secret explicitly so a developer's .env cannot loosen these tests
    — the failure mode being a fail-closed assertion that quietly stops
    asserting anything. There is no opt-out left to pin."""
    _reset_settings()
    s = get_settings()
    object.__setattr__(s, "internal_auth_secret", value)


# ── identity ──────────────────────────────────────────────────────────

def test_identity_from_forwarded_jwt_unverified():
    # Secret configured + presented: this test is about identity decoding.
    _set_secret("s3cret")
    ctx = _call(token=_token({"sub": "u-123", "org": "acme", "account_id": "atl-9"}),
                internal="s3cret")
    assert isinstance(ctx, UserContext)
    assert ctx.user_id == "u-123" and ctx.org == "acme"
    assert ctx.jira_account_id == "atl-9"
    assert ctx.allowed_scopes == []  # scopes come from the body, not here


def test_missing_token_rejected():
    # Secret satisfied, so the failure under test is the missing token itself.
    _set_secret("s3cret")
    with pytest.raises(AuthError):
        _call(token=None, internal="s3cret")


# ── internal-secret enforcement ───────────────────────────────────────

def test_secret_set_requires_match():
    _set_secret("topsecret")
    # correct secret → ok
    ctx = _call(token=_token({"sub": "u"}), internal="topsecret")
    assert ctx.user_id == "u"


def test_secret_set_wrong_value_rejected():
    _set_secret("topsecret")
    with pytest.raises(AuthError):
        _call(token=_token({"sub": "u"}), internal="WRONG")


def test_secret_set_missing_header_rejected():
    _set_secret("topsecret")
    with pytest.raises(AuthError):
        _call(token=_token({"sub": "u"}), internal=None)


def test_secret_unset_refuses_everything():
    """Unset secret is FAIL-CLOSED (was permissive until 2026-07-21).

    require_gateway does not verify the JWT signature, so this secret is the only
    thing between a direct caller and the graph. There is no bypass: the dev
    opt-out that briefly existed was removed on 2026-07-22.
    """
    from fastapi import HTTPException

    _set_secret("")
    with pytest.raises(HTTPException) as exc:
        _call(token=_token({"sub": "u"}), internal=None)
    assert exc.value.status_code == 503


def test_no_opt_out_exists():
    """The dev escape hatch was removed 2026-07-22 — nothing may bring it back."""
    from core.settings import Settings

    assert not hasattr(Settings(), "allow_unauthenticated_gateway")


def test_expired_token_rejected():
    """The engine delegates *signature* trust to the gateway, not *freshness*.

    PyJWT turns off every check when verify_signature is off, so without an
    explicit verify_exp a leaked-but-expired token would still be honoured if
    the gateway ever forwarded one. This is the regression guard for that.
    """
    import time
    from core.exceptions import TokenInvalidError

    _set_secret("shared-secret")
    expired = _token({"sub": "u1", "org": "acme", "exp": int(time.time()) - 60})
    with pytest.raises(TokenInvalidError):
        _call(token=expired, internal="shared-secret")


def test_unexpired_token_still_accepted():
    """Guards the fix from over-reaching: a live token must keep working."""
    import time

    _set_secret("shared-secret")
    fresh = _token({"sub": "u1", "org": "acme", "exp": int(time.time()) + 300})
    ctx = _call(token=fresh, internal="shared-secret")
    assert isinstance(ctx, UserContext)
    assert ctx.user_id == "u1"
