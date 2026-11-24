"""
common/vault_client.py is fail-closed: any resolution failure raises, and a
failed resolution must fail the run — never fall back to the server's env
(which may belong to a different client).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from common.config import Settings
from common.vault_client import VaultProxyError, get_tenant_creds, resolve_run_env


def _settings(**kw) -> Settings:
    kw.setdefault("vault_proxy_url", "http://vault-proxy:8080")
    kw.setdefault("vault_proxy_token", "tok")
    return Settings(_env_file=None, **kw)


def _resp(status: int, body):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = body
    return m


# ── fail-closed ───────────────────────────────────────────────────────

def test_unset_url_refuses():
    with pytest.raises(VaultProxyError, match="VAULT_PROXY_URL"):
        get_tenant_creds("ezobooks", "neo4j", _settings(vault_proxy_url=""))


def test_unknown_type_refuses():
    with pytest.raises(VaultProxyError, match="unknown credential type"):
        get_tenant_creds("ezobooks", "mongo", _settings())


@pytest.mark.parametrize("status,needle", [(401, "token"), (404, "404"), (502, "502")])
def test_http_errors_refuse(status, needle):
    with patch("common.vault_client.requests.get", return_value=_resp(status, {})):
        with pytest.raises(VaultProxyError, match=needle):
            get_tenant_creds("ezobooks", "neo4j", _settings())


def test_unreachable_refuses():
    with patch(
        "common.vault_client.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(VaultProxyError, match="unreachable"):
            get_tenant_creds("ezobooks", "neo4j", _settings())


def test_empty_creds_refuse():
    with patch("common.vault_client.requests.get", return_value=_resp(200, {})):
        with pytest.raises(VaultProxyError, match="empty"):
            get_tenant_creds("ezobooks", "neo4j", _settings())


def test_missing_required_keys_refuse():
    # neo4j entry present but without a password → refuse, don't half-connect.
    with patch(
        "common.vault_client.requests.get",
        return_value=_resp(200, {"uri": "bolt://x:7687"}),
    ):
        with pytest.raises(VaultProxyError, match="missing required keys"):
            resolve_run_env("ezobooks", settings=_settings())


# ── happy path ────────────────────────────────────────────────────────

def test_resolve_run_env_maps_to_runner_env():
    bodies = {
        "neo4j": {"uri": "bolt://client:7687", "username": "neo4j", "password": "n4jpw"},
        "postgres": {"host": "db.client", "port": "5433", "user": "lore",
                     "password": "p@ss/w", "dbname": "lore_data"},
    }

    def fake_get(url, **kw):
        assert kw["headers"]["X-Proxy-Token"] == "tok"
        return _resp(200, bodies[url.rsplit("/", 1)[-1]])

    with patch("common.vault_client.requests.get", side_effect=fake_get):
        env = resolve_run_env("ezobooks", settings=_settings())

    assert env["DATABASE_URL"] == "postgresql://lore:p%40ss%2Fw@db.client:5433/lore_data"
    # ONE graph per tenant: `tenants/{org}/neo4j` IS the claims graph, so it
    # fills the CLAIMS_NEO4J_* vars project_claims.py reads.
    assert env["CLAIMS_NEO4J_URI"] == "bolt://client:7687"
    assert env["CLAIMS_NEO4J_PASSWORD"] == "n4jpw"
    assert env["CLAIMS_NEO4J_USERNAME"] == "neo4j"
    assert "NEO4J_URI" not in env, "the legacy triple graph is not provisioned at all"
    # The stamp assert_connection_for_org checks before any DB write.
    assert env["LORE_CONNECTION_ORG"] == "ezobooks"


def test_postgres_uri_key_wins_verbatim():
    bodies = {
        "neo4j": {"uri": "bolt://c:7687", "password": "x"},
        "postgres": {"uri": "postgresql://u:p@h:5432/d"},
    }
    with patch(
        "common.vault_client.requests.get",
        side_effect=lambda url, **kw: _resp(200, bodies[url.rsplit("/", 1)[-1]]),
    ):
        env = resolve_run_env("ezobooks", settings=_settings())
    assert env["DATABASE_URL"] == "postgresql://u:p@h:5432/d"


# ── per-source connector secrets ──────────────────────────────────────

def test_zoho_source_creds_resolve_to_env_only_for_zoho():
    bodies = {
        "neo4j": {"uri": "bolt://c:7687", "password": "x"},
        "postgres": {"uri": "postgresql://u:p@h/d"},
        "zoho": {"client_id": "cid", "client_secret": "sec", "refresh_token": "rt",
                 "data_center": "in"},
    }
    with patch("common.vault_client.requests.get",
               side_effect=lambda url, **kw: _resp(200, bodies[url.rsplit("/", 1)[-1]])):
        env = resolve_run_env("ezobooks", source="zoho_mail", settings=_settings())
    assert env["ZOHO_CLIENT_ID"] == "cid" and env["ZOHO_CLIENT_SECRET"] == "sec"
    assert env["ZOHO_REFRESH_TOKEN"] == "rt" and env["ZOHO_DATA_CENTER"] == "in"
    assert "ZOHO_ORG_ID" not in env, "optional key absent from vault stays unset"


def test_gmail_run_never_fetches_zoho_creds():
    """A source with no connector secret (gmail) must not hit the zoho endpoint —
    otherwise every Gmail run would fail on tenants that never configured Zoho."""
    seen = []

    def fake_get(url, **kw):
        typ = url.rsplit("/", 1)[-1]
        seen.append(typ)
        return _resp(200, {"neo4j": {"uri": "bolt://c:7687", "password": "x"},
                           "postgres": {"uri": "postgresql://u:p@h/d"}}[typ])

    with patch("common.vault_client.requests.get", side_effect=fake_get):
        resolve_run_env("ezobooks", source="gmail", settings=_settings())
    assert "zoho" not in seen, "gmail run must never request zoho creds"


def test_zoho_missing_oauth_field_refuses():
    bodies = {
        "neo4j": {"uri": "bolt://c:7687", "password": "x"},
        "postgres": {"uri": "postgresql://u:p@h/d"},
        "zoho": {"client_id": "cid", "client_secret": "sec"},   # no refresh_token
    }
    with patch("common.vault_client.requests.get",
               side_effect=lambda url, **kw: _resp(200, bodies[url.rsplit("/", 1)[-1]])):
        with pytest.raises(VaultProxyError, match="missing required keys"):
            resolve_run_env("ezobooks", source="zoho_mail", settings=_settings())


def test_zoho_missing_vault_entry_refuses():
    """Vault is the ONLY store for connector credentials, so "no entry" means
    this tenant is not configured for Zoho and the run must stop. Tolerating the
    404 would let it continue and fetch with whatever token is ambient on the
    box — the client onboarded before this one."""
    def fake_get(url, **kw):
        typ = url.rsplit("/", 1)[-1]
        if typ == "zoho":
            return _resp(404, {})
        return _resp(200, {"neo4j": {"uri": "bolt://c:7687", "password": "x"},
                           "postgres": {"uri": "postgresql://u:p@h/d"}}[typ])

    with patch("common.vault_client.requests.get", side_effect=fake_get):
        with pytest.raises(VaultProxyError, match="404"):
            resolve_run_env("ezobooks", source="zoho_mail", settings=_settings())


def test_zoho_entry_missing_client_id_refuses():
    """All three OAuth values are per-tenant on the self-client path — a
    partially-seeded Vault entry is a misconfiguration, not a partial success."""
    bodies = {
        "neo4j": {"uri": "bolt://c:7687", "password": "x"},
        "postgres": {"uri": "postgresql://u:p@h/d"},
        "zoho": {"refresh_token": "rt", "data_center": "in"},   # no client_id/secret
    }
    with patch("common.vault_client.requests.get",
               side_effect=lambda url, **kw: _resp(200, bodies[url.rsplit("/", 1)[-1]])):
        with pytest.raises(VaultProxyError, match="missing required keys"):
            resolve_run_env("ezobooks", source="zoho_mail", settings=_settings())


def test_zoho_build_command_uses_ledger_sync_and_no_inline_creds():
    from control.manager import _build_command
    from control.models import RunSpec

    cmd = _build_command(RunSpec(org="ezobooks", source="zoho_mail"), None)
    assert cmd[-3:] == ["runners.zoho_ledger_sync", "--org", "ezobooks"]
    assert not any("secret" in c.lower() or "token" in c.lower() for c in cmd), \
        "creds flow via env, never on the argv"


# ── manager wiring ────────────────────────────────────────────────────

def test_manager_vault_env_off_when_url_unset():
    from control.manager import RunManager

    with patch("control.manager.get_settings", return_value=_settings(vault_proxy_url="")):
        assert RunManager.__new__(RunManager)._vault_env("ezobooks") == {}


def test_manager_vault_failure_propagates():
    """Proxy configured but resolution fails → the error propagates (start()
    marks the run failed); it must NOT degrade to the server's own env."""
    from control.manager import RunManager

    with patch("control.manager.get_settings", return_value=_settings()), \
         patch("control.manager.resolve_run_env", side_effect=VaultProxyError("down")):
        with pytest.raises(VaultProxyError):
            RunManager.__new__(RunManager)._vault_env("ezobooks")


def test_missing_neo4j_entry_refuses():
    """The projection must not fall through to whatever graph the box holds —
    which on a shared host is another client's."""
    def fake_get(url, **kw):
        typ = url.rsplit("/", 1)[-1]
        if typ == "neo4j":
            return _resp(404, {})
        return _resp(200, {"neo4j": {"uri": "bolt://c:7687", "password": "x"},
                           "postgres": {"uri": "postgresql://u:p@h/d"}}[typ])

    with patch("common.vault_client.requests.get", side_effect=fake_get):
        with pytest.raises(VaultProxyError, match="neo4j"):
            resolve_run_env("ezobooks", settings=_settings())


# ── daemon wiring (runners/substrate_flow) ────────────────────────────
# The daemon runs every stage AFTER the fetch and is not launched by the control
# API, so it never passes through RunManager._child_env. If it read the box's own
# DATABASE_URL it would write one tenant's knowledge into another's graph.

def test_daemon_startup_repoints_databases_at_the_tenant(monkeypatch):
    import common.config as cfg
    from builders.l0_ledger import _resolve_dsn
    from runners.substrate_flow import _apply_tenant_env

    monkeypatch.setenv("VAULT_PROXY_URL", "http://vault-proxy:8080")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@wrong-tenant:5432/db")
    monkeypatch.setenv("CLAIMS_NEO4J_URI", "bolt://wrong-tenant:7688")
    try:
        cfg._reset_settings()
        monkeypatch.setattr(
            "common.vault_client.resolve_run_env",
            lambda org, settings=None: {
                "DATABASE_URL": "postgresql://u:p@right-tenant:5432/db",
                "CLAIMS_NEO4J_URI": "bolt://right-tenant:7688",
                "LORE_CONNECTION_ORG": org,
            },
        )
        _apply_tenant_env("expertedge")
        # _resolve_dsn reads os.environ first — the L0 replica must be the
        # tenant's, not the box's.
        assert _resolve_dsn() == "postgresql://u:p@right-tenant:5432/db"
        # …and the cached Settings singleton must have been rebuilt, or every
        # later stage keeps resolving the stale ambient values.
        assert cfg.get_settings().claims_neo4j_uri == "bolt://right-tenant:7688"
    finally:
        cfg._reset_settings()


def test_daemon_without_vault_leaves_the_environment_alone(monkeypatch):
    """Local / single-tenant: no proxy configured, behaviour unchanged."""
    import common.config as cfg
    from runners.substrate_flow import _apply_tenant_env

    monkeypatch.setenv("VAULT_PROXY_URL", "")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/local")
    try:
        cfg._reset_settings()
        monkeypatch.setattr(
            "common.vault_client.resolve_run_env",
            lambda org, settings=None: pytest.fail("must not call vault"),
        )
        _apply_tenant_env("ezobooks")
        assert os.environ["DATABASE_URL"] == "postgresql://u:p@localhost:5432/local"
    finally:
        cfg._reset_settings()


def test_data_store_env_is_stripped_when_vault_configured(monkeypatch):
    """The other half of making `neo4j` optional: absence must mean 'unset', not
    'inherit the box's'. Otherwise a legacy build on a tenant with no entry
    writes into whichever graph this host was pointed at."""
    from control.manager import RunManager
    from control.models import RunSpec

    monkeypatch.setenv("NEO4J_URI", "bolt://some-other-clients-graph:7687")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@some-other-client/db")
    with patch("control.manager.get_settings", return_value=_settings()):
        env = RunManager()._child_env(RunSpec(org="expertedge", source="zoho_mail"), {})
    assert "NEO4J_URI" not in env
    assert "DATABASE_URL" not in env
