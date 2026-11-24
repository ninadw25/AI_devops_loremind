"""
common/vault_client.py
──────────────────────
Client for vault-proxy — the per-tenant credential service
(`GET /creds/:tenant/:type`, X-Proxy-Token auth). Python twin of
elysia-rbac's `src/registry/vault.ts`.

Ingestion is one-tenant-per-process, so creds are resolved ONCE at run
launch (control/manager.py) and injected into the runner subprocess's
environment — the runners keep reading env exactly as before. No cache:
every run is a fresh process.

Fail-closed: any failure (unset URL, unreachable proxy, bad token, missing
tenant, empty secret) raises VaultProxyError. There is never a fallback to
the server's own environment — that would silently run one client's
ingestion against another client's databases.
"""

from __future__ import annotations

from urllib.parse import quote

import requests

from common.config import Settings, get_settings

# The credential kinds vault-proxy serves: DB/storage routing + per-source
# connector secrets. A source's secret is resolved ONLY for that source's runs
# (a Gmail run never asks for zoho), so source creds never gate an unrelated run.
#
# Not an allowlist the proxy shares: vault-proxy validates `type` as a slug and
# reads whatever is at <mount>/<prefix>/<tenant>/<type>, so adding a source is a
# Vault write plus one SOURCE_CRED_ENV entry — no proxy deploy. This tuple only
# guards against a typo'd type here becoming a 404 at run time.
ALLOWED_TYPES = ("s3", "postgres", "neo4j", "zoho")

# source → (vault cred type, {vault key → runner ENV var}). The runner reads
# these env vars via common/config.py exactly as in local dev; the control
# plane just supplies the tenant's values from vault at launch.
#
# All five zoho keys are per-tenant: the client registers their OWN self-client
# app, so client_id/client_secret are theirs, not Loremind's. `refresh_token`
# must be an EXCHANGED token — the one-time `code` Zoho's API console emits
# expires within minutes and fails as invalid_code.
SOURCE_CRED_ENV: dict[str, tuple[str, dict[str, str]]] = {
    "zoho_mail": ("zoho", {
        "client_id": "ZOHO_CLIENT_ID",
        "client_secret": "ZOHO_CLIENT_SECRET",
        "refresh_token": "ZOHO_REFRESH_TOKEN",
        "data_center": "ZOHO_DATA_CENTER",
        "org_id": "ZOHO_ORG_ID",
        # "true" for a tenant with exactly one mailbox. Without it, a tenant
        # carrying an org_id (which Cliq needs) is treated as org-wide and the
        # run fails rather than quietly fetching one inbox.
        "mail_single_mailbox": "ZOHO_MAIL_SINGLE_MAILBOX",
    }),
}

# Keys without which an entry is a misconfiguration, not a partial one.
# data_center and org_id are excluded: the runner defaults the first, and only
# Cliq discovery needs the second.
_REQUIRED_CRED_KEYS: dict[str, tuple[str, ...]] = {
    "zoho": ("client_id", "client_secret", "refresh_token"),
}

_TIMEOUT_S = 5.0  # matches the proxy's own 5s Vault timeout


class VaultProxyError(RuntimeError):
    """Credential resolution failed — the run must not start."""


class VaultEntryMissingError(VaultProxyError):
    """The tenant has no entry of this type in Vault (404). Distinct from an
    outage so callers can treat "no entry" as normal without also swallowing
    a dead proxy."""


def get_tenant_creds(org: str, typ: str, settings: Settings | None = None) -> dict:
    """Fetch `org`'s credentials of kind `typ` from vault-proxy.

    Returns the raw key/value dict stored in Vault (no fixed schema).
    Raises VaultProxyError on ANY failure — never returns a partial or
    empty result.
    """
    if typ not in ALLOWED_TYPES:
        raise VaultProxyError(f"unknown credential type {typ!r} (allowed: {ALLOWED_TYPES})")
    if not org:
        raise VaultProxyError("org is required")

    s = settings or get_settings()
    if not s.vault_proxy_url:
        raise VaultProxyError(
            "VAULT_PROXY_URL is not set — cannot resolve tenant credentials"
        )

    url = f"{s.vault_proxy_url.rstrip('/')}/creds/{quote(org, safe='')}/{typ}"
    headers = {}
    if s.vault_proxy_token:
        headers["X-Proxy-Token"] = s.vault_proxy_token

    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT_S)
    except requests.RequestException as exc:
        raise VaultProxyError(f"vault-proxy unreachable for {org}/{typ}: {exc}") from exc

    if resp.status_code == 401:
        raise VaultProxyError("vault-proxy rejected the proxy token (401)")
    if resp.status_code == 404:
        raise VaultEntryMissingError(
            f"no {typ} credentials in Vault for tenant {org!r} (404)"
        )
    if resp.status_code != 200:
        raise VaultProxyError(
            f"vault-proxy error for {org}/{typ}: HTTP {resp.status_code}"
        )

    data = resp.json()
    if not isinstance(data, dict) or not data:
        raise VaultProxyError(f"vault-proxy returned empty credentials for {org}/{typ}")
    return data


def _require(creds: dict, org: str, typ: str, keys: tuple[str, ...]) -> None:
    missing = [k for k in keys if not creds.get(k)]
    if missing:
        raise VaultProxyError(
            f"Vault entry for {org}/{typ} is missing required keys: {missing}"
        )


def resolve_postgres_dsn(org: str, settings: Settings | None = None) -> str:
    """`org`'s Postgres DSN from Vault, with user+password URL-encoded.

    Split out of resolve_run_env because read-only callers (the control API's
    observability endpoints) need the tenant's database and nothing else —
    fetching neo4j too would make a dashboard poll fail on a tenant whose graph
    is not provisioned yet.
    """
    pg = get_tenant_creds(org, "postgres", settings)
    if pg.get("uri"):
        return str(pg["uri"])
    _require(pg, org, "postgres", ("host", "user", "password", "dbname"))
    user = quote(str(pg["user"]), safe="")
    pw = quote(str(pg["password"]), safe="")
    port = pg.get("port", "5432")
    return f"postgresql://{user}:{pw}@{pg['host']}:{port}/{pg['dbname']}"


def resolve_run_env(org: str, source: str | None = None,
                    settings: Settings | None = None) -> dict[str, str]:
    """Resolve `org`'s neo4j + postgres creds (always) plus the connector secret
    for `source` (when that source needs one), returned as the env vars the
    runners already read — plus the LORE_CONNECTION_ORG stamp that
    `common.config.assert_connection_for_org` checks before any DB write.

    This IS the "get the .env at runtime" step: the returned dict is merged
    into the runner subprocess's environment by control/manager.py. Source
    secrets are resolved ONLY for their own source, so a Gmail run never fails
    because the tenant has no Zoho entry in vault.
    """
    dsn = resolve_postgres_dsn(org, settings)

    # ONE graph per tenant. The substrate pipeline projects claims and nothing
    # else (runners/substrate_flow → project_claims), so `tenants/{org}/neo4j`
    # IS the claims graph — which is why it fills CLAIMS_NEO4J_*, the vars
    # builders/project_claims.py reads.
    #
    # The legacy triple graph (builders/unified_builder.py → NEO4J_*) is not
    # resolved here at all: no tenant onboarded onto the substrate has one, and
    # control/manager.py strips NEO4J_* from the inherited environment whenever
    # vault is configured, so a legacy build against such a tenant fails on an
    # unset URI instead of writing into whichever graph the box was pointed at.
    neo4j = get_tenant_creds(org, "neo4j", settings)
    _require(neo4j, org, "neo4j", ("uri", "password"))

    env = {
        "CLAIMS_NEO4J_URI": str(neo4j["uri"]),
        "CLAIMS_NEO4J_USERNAME": str(neo4j.get("username", "neo4j")),
        "CLAIMS_NEO4J_PASSWORD": str(neo4j["password"]),
        "DATABASE_URL": dsn,
        "LORE_CONNECTION_ORG": org,
    }

    src_cred = SOURCE_CRED_ENV.get(source or "")
    if src_cred:
        vault_type, key_to_env = src_cred
        # No 404 tolerance: Vault is the ONLY store for connector credentials,
        # so "no entry" means this tenant is not configured for this source and
        # the run must stop. Swallowing it would let the run continue and fetch
        # with whatever token is ambient on the box — another client's.
        creds = get_tenant_creds(org, vault_type, settings)
        _require(creds, org, vault_type, _REQUIRED_CRED_KEYS.get(vault_type, ()))
        for vault_key, env_var in key_to_env.items():
            if creds.get(vault_key):
                env[env_var] = str(creds[vault_key])

    return env
