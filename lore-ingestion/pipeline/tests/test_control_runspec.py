"""
RunSpec is the control API's trust boundary: the shared internal secret proves
the caller is the control plane, not that its field values are safe. These tests
lock the validation that keeps a run from choosing the runner's code, its TLS
trust, its tenant stamp, or a path outside the data dir.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.config import Settings
from control.manager import _CONTROL_PLANE_SECRETS, RunManager, _build_command
from control.models import RunSpec


def _spec(**kw) -> RunSpec:
    return RunSpec(org="ezobooks", source="zoho_mail", **kw)


# ── org ──────────────────────────────────────────────────────────────

def test_org_rejects_path_traversal():
    for bad in ("../../etc", "a/b", "..", "ORG", "org with space", ""):
        with pytest.raises(ValidationError):
            RunSpec(org=bad)


def test_org_accepts_a_slug():
    assert RunSpec(org="ezobooks-2").org == "ezobooks-2"


# ── env allowlist ────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "PATH", "PYTHONPATH", "LD_PRELOAD", "HTTPS_PROXY",
    "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "LORE_CONNECTION_ORG",
])
def test_env_rejects_dangerous_keys(key):
    with pytest.raises(ValidationError):
        _spec(env={key: "attacker-controlled"})


def test_env_allows_tenant_routing_and_connector_secrets():
    spec = _spec(env={"NEO4J_URI": "bolt://x", "ZOHO_REFRESH_TOKEN": "t"})
    assert spec.env["ZOHO_REFRESH_TOKEN"] == "t"


# ── data_dir containment ─────────────────────────────────────────────

def test_data_dir_must_stay_under_the_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LORE_DATA_ROOT", str(tmp_path))
    inside = tmp_path / "ezobooks"
    assert _spec(data_dir=str(inside)).data_dir == str(inside.resolve())
    with pytest.raises(ValidationError):
        _spec(data_dir="/etc")
    with pytest.raises(ValidationError):
        _spec(data_dir=str(tmp_path / ".." / "elsewhere"))


# ── argv splat ───────────────────────────────────────────────────────

def test_list_args_cannot_smuggle_a_flag():
    with pytest.raises(ValidationError):
        RunSpec(org="ezobooks", org_domains=["--data-dir"])


# ── child environment ────────────────────────────────────────────────

def test_child_env_strips_control_plane_secrets(monkeypatch):
    for k in _CONTROL_PLANE_SECRETS:
        monkeypatch.setenv(k, "super-secret")
    env = RunManager()._child_env(_spec(), {})
    assert not (_CONTROL_PLANE_SECRETS & env.keys())


def test_vault_creds_beat_spec_env_and_org_stamp_is_server_set():
    spec = _spec(env={"NEO4J_URI": "bolt://attacker"})
    env = RunManager()._child_env(spec, {"NEO4J_URI": "bolt://tenant"})
    assert env["NEO4J_URI"] == "bolt://tenant"
    assert env["LORE_CONNECTION_ORG"] == "ezobooks"


def test_vault_configured_strips_the_hosts_own_connector_creds(monkeypatch):
    """The box was provisioned with one client's Zoho credentials in its .env.
    Once vault-proxy is configured, Vault is the only permitted source, so a
    second tenant's run must not inherit the first tenant's credential and fetch
    their mail under its own org stamp."""
    monkeypatch.setenv("ZOHO_REFRESH_TOKEN", "first-clients-token")
    monkeypatch.setenv("ZOHO_CLIENT_ID", "first-clients-app")
    monkeypatch.setenv("SLACK_TOKEN", "xoxp-first-client")
    # Box-level tuning is not tenant-scoping and must survive — stripping it
    # would silently change how far back a first sync reaches.
    monkeypatch.setenv("SLACK_SCANBACK_DAYS", "90")
    monkeypatch.setattr(
        "control.manager.get_settings",
        lambda: Settings(_env_file=None, vault_proxy_url="http://vault-proxy:8080"),
    )
    env = RunManager()._child_env(_spec(), {})
    assert "ZOHO_REFRESH_TOKEN" not in env
    assert "ZOHO_CLIENT_ID" not in env
    assert "SLACK_TOKEN" not in env
    assert env["SLACK_SCANBACK_DAYS"] == "90"


def test_vault_creds_replace_the_stripped_ones(monkeypatch):
    monkeypatch.setenv("ZOHO_REFRESH_TOKEN", "first-clients-token")
    monkeypatch.setattr(
        "control.manager.get_settings",
        lambda: Settings(_env_file=None, vault_proxy_url="http://vault-proxy:8080"),
    )
    env = RunManager()._child_env(_spec(), {"ZOHO_REFRESH_TOKEN": "this-tenants"})
    assert env["ZOHO_REFRESH_TOKEN"] == "this-tenants"


def test_no_vault_keeps_local_env_working(monkeypatch):
    """Local dev has no vault-proxy and must keep reading .env as before."""
    monkeypatch.setenv("ZOHO_REFRESH_TOKEN", "local-dev-token")
    monkeypatch.setattr(
        "control.manager.get_settings",
        lambda: Settings(_env_file=None, vault_proxy_url=""),
    )
    assert RunManager()._child_env(_spec(), {})["ZOHO_REFRESH_TOKEN"] == "local-dev-token"


def test_zoho_command_carries_no_inline_creds():
    cmd = _build_command(RunSpec(org="ezobooks", source="zoho_mail"), None)
    assert cmd[-3:] == ["runners.zoho_ledger_sync", "--org", "ezobooks"]
    assert not any("secret" in c.lower() or "token" in c.lower() for c in cmd), \
        "creds flow via env, never on the argv"
