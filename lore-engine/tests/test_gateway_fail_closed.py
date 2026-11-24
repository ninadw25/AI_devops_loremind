"""require_gateway is fail-closed, with no bypass of any kind.

These routes decode the caller's JWT *without verifying its signature* — the
gateway is supposed to have done that. So the internal secret is the only thing
between a direct caller and every tenant's knowledge graph.

History this guards against:
  * an unset secret used to mean "enforcement off". Verified by hand against a
    running engine on 2026-07-21: a token signed with a foreign key returned
    real customer data.
  * a dev opt-out flag (ALLOW_UNAUTHENTICATED_GATEWAY) briefly replaced it.
    Also removed — there is now no way to run lore-engine without a secret.
"""

import jwt
import pytest
from fastapi import HTTPException

from api.auth import require_gateway
from core.exceptions import AuthError
from core.settings import Settings, _reset_settings


def _token(secret: str = "irrelevant-the-signature-is-never-checked") -> str:
    return jwt.encode({"sub": "someone@example.com", "org": "acme"}, secret, algorithm="HS256")


class _Creds:
    """Stands in for HTTPAuthorizationCredentials."""

    def __init__(self, token: str) -> None:
        self.credentials = token


@pytest.fixture(autouse=True)
def _pin_closed_baseline(monkeypatch):
    """Pin a known-closed baseline the developer's .env cannot loosen.

    Settings reads `.env` as a fallback, so deleting the var is NOT enough — a
    local INTERNAL_AUTH_SECRET would flow back in and silently turn the
    regression tests below into no-ops. That is the same class of bug as the one
    under test, so set it explicitly; a real env var beats the .env file.
    """
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", "")
    monkeypatch.setenv("ENVIRONMENT", "development")
    _reset_settings()
    yield
    _reset_settings()


async def test_unset_secret_refuses_everything():
    """The regression test: no secret means serve nobody."""
    with pytest.raises(HTTPException) as exc:
        await require_gateway(credentials=_Creds(_token()), x_internal_auth=None)
    assert exc.value.status_code == 503
    assert "INTERNAL_AUTH_SECRET" in exc.value.detail


async def test_forged_token_no_longer_gets_in():
    """A token signed with a key we never issued must not be served."""
    forged = _token("a-secret-the-engine-has-never-seen")
    with pytest.raises(HTTPException) as exc:
        await require_gateway(credentials=_Creds(forged), x_internal_auth=None)
    assert exc.value.status_code == 503


async def test_no_env_var_can_reopen_the_door(monkeypatch):
    """Guards the removal of the opt-out.

    Settings ignores unknown keys, so a leftover ALLOW_UNAUTHENTICATED_GATEWAY
    in someone's .env is silently inert rather than quietly re-enabling the
    bypass. If anyone reintroduces such a flag, this test fails.
    """
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_GATEWAY", "true")
    monkeypatch.setenv("QUERY_OPEN_ACCESS", "true")
    _reset_settings()

    with pytest.raises(HTTPException) as exc:
        await require_gateway(credentials=_Creds(_token()), x_internal_auth=None)
    assert exc.value.status_code == 503


async def test_configured_secret_is_enforced(monkeypatch):
    """With a secret set, wrong/missing is rejected and correct passes."""
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", "the-real-secret")
    _reset_settings()

    with pytest.raises(AuthError):
        await require_gateway(credentials=_Creds(_token()), x_internal_auth="wrong")
    with pytest.raises(AuthError):
        await require_gateway(credentials=_Creds(_token()), x_internal_auth=None)

    ctx = await require_gateway(credentials=_Creds(_token()), x_internal_auth="the-real-secret")
    assert ctx.user_id == "someone@example.com"
    assert ctx.org == "acme"
    assert ctx.allowed_scopes == []  # scopes arrive in the body, never the token


def test_production_refuses_to_boot_without_a_secret(monkeypatch):
    """A prod deploy that cannot be secure fails at startup, not at request time."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", "")
    with pytest.raises(ValueError, match="INTERNAL_AUTH_SECRET"):
        Settings()


def test_legacy_password_login_is_gone():
    """The public POST /api/auth/login that minted tokens from env password
    hashes was removed; nothing may reintroduce a second way in."""
    import api.auth as auth

    for gone in ("login", "create_token", "decode_token", "get_current_user",
                 "get_users", "get_user_orgs"):
        assert not hasattr(auth, gone), f"legacy auth helper {gone!r} is back"

    with pytest.raises(ModuleNotFoundError):
        __import__("api.routes.admin")
