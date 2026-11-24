"""
common/config.py
────────────────
Single source of truth for configuration. Mirrors the pattern used in
`lore-engine/core/settings.py`.

This module is the ONE place that should call `os.getenv()` once the
migration in Step 5 of `docs/modularity-phases/` is complete. For now it
runs in parallel with the existing scattered `os.getenv` calls — adding
it is additive and changes no runtime behavior.

Usage:
    from common.config import get_settings, validate_for

    settings = get_settings()
    settings.nvidia_api_key

    # At the top of each stage runner:
    validate_for("triple_extractor")   # raises if required vars missing

The module-level singleton is built lazily on first call to
`get_settings()` and reused thereafter. In tests, call `_reset_settings()`
to force re-initialisation with different env values.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    Field names are lowercase; env vars are matched case-insensitively
    (so `NVIDIA_API_KEY` in `.env` populates `nvidia_api_key`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ────────────────────────────────────────────────────────────
    org: str = ""
    output_dir: str = ""   # empty → "<repo>/output"

    # ── Control API (docs/HYBRID_DEPLOYMENT.md §5) ──────────────────────
    # Thin FastAPI server (control/app.py) that lets the control plane trigger
    # ingestion runs and set schedules. Not a persistent worker — it launches
    # the existing runners as subprocess jobs and reports status.
    control_port: int = 8100
    # X-Internal-Auth shared secret the control plane sends. Empty → the
    # control API refuses every request (503, fail-closed). MUST be set.
    control_internal_secret: str = ""
    # vault-proxy — per-tenant credential service (common/vault_client.py) and
    # the SINGLE store for anything per-tenant. When set, control/manager.py
    # resolves each run's NEO4J_*/DATABASE_URL *and* its connector secret
    # (ZOHO_*, …) from the proxy at launch, and strips those same variables from
    # the host's own environment so a run can never inherit another tenant's
    # token. Resolution failure fails the run — never falls back to local env.
    vault_proxy_url: str = ""            # e.g. http://vault-proxy:8080
    vault_proxy_token: str = ""          # sent as X-Proxy-Token
    # Where to call back when a run completes (elysia-rbac ingestion-complete
    # webhook + run-status callback). Empty → callbacks skipped.
    rbac_callback_url: str = ""          # e.g. http://elysia-rbac:3000
    rbac_internal_secret: str = ""       # Bearer secret for the callbacks

    # Per-org artifact bucket (SSE-KMS). When set, the control API uploads the
    # identity + scope_principals artifacts to s3://{bucket}/{org}/... after a
    # successful run, so elysia-rbac (ARTIFACT_SOURCE=s3) can load them. Empty →
    # local/EFS handoff (no upload).
    s3_artifact_bucket: str = ""
    s3_region: str = "ap-south-1"

    # ── LLM (extraction stage uses NVIDIA Build by default) ───────────
    # The commented-out OpenRouter/Gemini path in
    # pipeline/triple_extractor.py reads the OPENROUTER_* settings below.
    # `llm_provider` selects which `infra/llm/` implementation the
    # registry hands back. Default to OpenRouter+Gemini (repo standard)
    # to align extraction with the main repo's model. NVIDIA remains
    # available for teams that prefer the DeepSeek path.
    llm_provider: Literal["nvidia", "openrouter", "bedrock"] = "bedrock"
    extraction_model: str = "deepseek-ai/deepseek-v3.2"
    local_model_url: str = ""

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    openai_api_key: str = ""

    # ── AWS Bedrock (Stage 5 extraction — DeepSeek V3.2, ap-south-1) ──
    # Uses Bedrock's OpenAI-compatible `bedrock-mantle` Chat Completions
    # endpoint with the Bedrock API key as the bearer token, so it drops
    # straight into the existing AsyncOpenAI stack. `bedrock_base_url`
    # empty → derived as https://bedrock-mantle.{region}.api.aws/v1.
    # DeepSeek V3.2 is In-Region available in ap-south-1 (no cross-region
    # inference profile needed); model id is the plain `deepseek.v3.2`.
    bedrock_api_key: str = ""
    bedrock_region: str = "ap-south-1"
    bedrock_base_url: str = ""
    bedrock_model: str = "deepseek.v3.2"
    # Conservative starting extraction budget. Bedrock throttles on BOTH
    # requests/min and tokens/min (model+region specific, adjustable via
    # Service Quotas). Start low; the adaptive limiter (P1) backs off on
    # ThrottlingException and these can be raised once real quota is known.
    bedrock_rpm: int = 30
    bedrock_tpm: int = 150_000
    bedrock_concurrency: int = 4

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embed_model: str = "openai/text-embedding-3-small"
    # Use Gemini 2.5 Flash as the repo-standard extraction model.
    openrouter_llm_model: str = "google/gemini-2.5-flash"
    openrouter_timeout_s: float = 30.0

    anthropic_api_key: str = ""   # used only by pipeline/filter.py LLM path

    # ── Embeddings (Stage 6.5) ────────────────────────────────────────
    # Keep embedding config separate from extraction config.
    # `extraction_model` is for Stage 5 triples; `embedding_model`
    # is for Stage 6.5 vector generation.
    embedding_provider: Literal["nvidia", "openai", "openrouter", "bedrock"] = "nvidia"
    embedding_model: str = "nvidia/nemotron-3-embed-1b"
    embedding_dim: int = 2048

    # ── Neo4j (Stage 8 — unified_builder upload pass) ────────────────
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""

    # ── Neo4j — claim substrate projection (build step 7) ────────────
    # Deliberately a SEPARATE instance from neo4j_uri above: the old
    # naked-triple ontology (:Person/:Decision/:Task/:Fact/:Event/:Topic/
    # :Organization) and the new claim projection (:Claim/:Person/:Topic/
    # :Artifact) must never share a database. See neo4j/docker-compose.claims.yml.
    claims_neo4j_uri: str = ""
    claims_neo4j_username: str = "neo4j"
    claims_neo4j_password: str = ""

    # ── Per-org connection routing guard ─────────────────────────────
    # This process is org-agnostic (see control/models.py:RunSpec): the control
    # plane resolves the client's connection from the registry and injects it
    # into the run's environment. Nothing about that env says *which client* it
    # was resolved for, so a hand-run `--org clientA` with clientB's .env
    # loaded writes clientA's knowledge into clientB's graph — silently, and it
    # looks like a clean run.
    #
    # `lore_connection_org` is the stamp the control plane sets alongside the
    # connection it resolved. When `require_registry_connection` is on, a run
    # must carry that stamp and it must name the org being built.
    #
    # Off by default so the documented CLI workflow keeps working locally; turn
    # it ON wherever more than one client's data exists. Mirrors lore-engine's
    # TENANT_ROUTING_ENABLED posture (fail-closed in prod, fail-open in dev).
    #
    # This stops accidents, not a determined operator — the stamp is an env var
    # and anyone can set it by hand. That's the intended bar: the risk here is
    # a mis-set .env, not an insider.
    require_registry_connection: bool = False
    lore_connection_org: str = ""

    # ── PostgreSQL + pgvector (chronograph store — the only backend) ─
    # Connection string: postgresql://user:pass@host:5432/dbname
    # DATABASE_URL (below) takes precedence over postgres_uri.
    # Default points at the shared client-side data DB (`lore_data`); the
    # chronograph store auto-creates/uses its own `chronograph` schema there.
    postgres_uri: str = "postgresql://lore:lore_secret@localhost:5433/lore_data"

    # ── Code platform credentials (chronograph) ──────────────────────
    gitlab_pat: str = ""
    gitlab_base_url: str = "https://gitlab.com"
    github_pat: str = ""

    # ── Chronograph tuning ──────────────────────────────────────────
    chronograph_db_name: str = "lore_data"
    chronograph_backfill_days: int = 90
    chronograph_max_file_size: int = 1024 * 1024
    gitlab_poll_interval_seconds: int = 3 * 60 * 60
    github_poll_interval_seconds: int = 3 * 60 * 60
    embed_batch_size: int = 50
    embed_delay_s: float = 1.5

    # ── PostgreSQL DATABASE_URL (standard libpq format) ───────────────
    # Takes precedence over postgres_uri. Chronograph is Postgres-only.
    database_url: str = ""

    # ── Slack (source connector) ─────────────────────────────────────
    slack_org: str = ""
    slack_token: str = ""
    slack_app_token: str = ""
    slack_ingest_mode: str = "polling"          # polling | socket | hybrid
    slack_scanback_days: int = 14
    slack_default_retry_sleep: float = 5.0
    # Raw string so an empty value falls through to app-tier auto-select,
    # matching the fetcher's original parse-with-fallback semantics.
    slack_request_delay: str = ""
    slack_app_tier: str = "marketplace"         # dev | tier3 | enterprise | marketplace | restricted
    slack_bot_allowlist: str = ""               # comma-separated bot user IDs
    slack_workspace_domain: str = ""

    # ── Jira (source connector) ──────────────────────────────────────
    jira_base_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_oauth_token: str = ""
    jira_pat: str = ""
    # Rate-limit / retry (Jira Cloud uses a cost-based budget + 429 +
    # Retry-After). `jira_request_delay` is a min inter-request spacing
    # (backpressure); `jira_max_retries` bounds 429/5xx/transport retries.
    jira_request_delay: float = 0.2
    jira_max_retries: int = 5

    # ── Microsoft 365 / Azure AD (Outlook + Teams connector) ────────────
    # Primary names (LOR-64 / LOR-65 — do NOT remove; fetcher.py reads these).
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
    # LOR-66 aliases — preferred going forward.  The helper `ms365_tenant()`
    # below resolves whichever pair is set; callers should use that helper
    # rather than reading these fields directly.
    ms365_tenant_id: str | None = None
    ms365_client_id: str | None = None
    ms365_client_secret: str | None = None
    # MS365 behavioural knobs (no azure_* equivalent — new in LOR-66).
    ms365_base_url: str = "https://graph.microsoft.com/v1.0"
    ms365_mail_scanback_days: int = 90
    ms365_teams_scanback_days: int = 30

    # ── Zoho (source connector: Mail + Cliq) ──────────────────────────
    # OAuth 2.0 self-client refresh-token flow (no redirect URI, suited to a
    # server-side connector). `zoho_data_center` selects the host suffix
    # (com/in/eu/au/jp) — must match where the Zoho account lives, else the
    # token endpoint returns invalid_code.
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_refresh_token: str = ""
    zoho_data_center: str = "com"        # com | in | eu | au | jp
    zoho_org_id: str = ""                # Cliq org id (admin discovery)
    # Opt OUT of org-wide mailbox enumeration for a tenant that genuinely has
    # one mailbox. Default false so a multi-mailbox org can never silently
    # degrade to the token owner's own inbox — the failure mode is a
    # complete-looking run over a fraction of the data. This is deliberately
    # NOT inferred from zoho_org_id: that field belongs to Cliq's admin
    # discovery, and a single-mailbox tenant still needs it.
    zoho_mail_single_mailbox: bool = False
    zoho_mail_scanback_days: int = 90
    zoho_cliq_scanback_days: int = 30

    # ── Gmail (source connector) rate-limit / batching ───────────────
    # Gmail charges quota units per method against ~250 units/user/sec.
    # messages.get costs 5, so a batch of N spends 5N units in one burst —
    # 50 lands exactly ON the ceiling and mostly 429s, and the inter-batch
    # pause can't help because the burst is instantaneous. Each 429'd message
    # is then refetched one at a time with backoff, which is the slow path.
    # Measured on a real mailbox (2026-07-17): 50 → 1.74 msg/s, 20 → 18.16,
    # 10 → 10.71 (pause-bound). 20 keeps the burst at 100 units and wins 10x.
    # Tune per Workspace: quota is per-user, so a busier tenant may want less.
    gmail_batch_size: int = 20            # messages per batch HTTP request
    gmail_batch_pause: float = 0.5        # seconds between batches
    gmail_rate_limit_delay: float = 0.1   # seconds before each individual fetch
    gmail_user_cooldown: float = 2.0      # seconds between users
    gmail_max_retries: int = 3            # 429 / network retry attempts
    # Without this a stalled Google socket blocks FOREVER: httplib2 leaves its
    # own timeout None, so the retry ladder below never gets an error to catch.
    # A real fetch hung 30min mid-mailbox this way (2026-07-17). Applied as the
    # process-wide socket default — httplib2 builds sockets with the bare
    # socket() constructor and only overrides a timeout it was given, so the
    # default is what reaches Google. Must exceed the slowest legitimate batch.
    gmail_socket_timeout: float = 180.0   # seconds before a dead socket errors
    # Pacing + parallelism (docs/PARALLEL_FETCH.md). The quota bucket is
    # PER MAILBOX, so pipelined batches within one mailbox share a token
    # bucket at 80% of the 250-unit ceiling, and different mailboxes don't
    # compete at all — cross-mailbox workers are effectively free throughput.
    gmail_units_per_sec: float = 200.0    # token-bucket pace per mailbox (80% of 250)
    gmail_pipeline_depth: int = 3         # in-flight batches per mailbox

    # ── Derived ─────────────────────────────────────────────────────

    @property
    def output_root(self) -> Path:
        """Resolved output directory. Defaults to <repo>/output."""
        if self.output_dir:
            return Path(self.output_dir)
        return Path(__file__).resolve().parent.parent / "output"

    # ── MS365 credential resolution helpers ─────────────────────────
    # Prefer ms365_* (LOR-66 naming); fall back to azure_* (LOR-64 naming).
    # Both naming schemes are fully supported — whichever pair is set in
    # .env will work.  Callers should use these properties instead of
    # reading azure_* or ms365_* directly so the fallback is applied once.

    @property
    def resolved_ms365_tenant_id(self) -> str | None:
        return self.ms365_tenant_id or self.azure_tenant_id

    @property
    def resolved_ms365_client_id(self) -> str | None:
        return self.ms365_client_id or self.azure_client_id

    @property
    def resolved_ms365_client_secret(self) -> str | None:
        return self.ms365_client_secret or self.azure_client_secret

    # ── Zoho derived hosts (data-center aware) ───────────────────────
    @property
    def zoho_accounts_url(self) -> str:
        return f"https://accounts.zoho.{self.zoho_data_center}"

    @property
    def zoho_mail_base_url(self) -> str:
        return f"https://mail.zoho.{self.zoho_data_center}/api"

    @property
    def zoho_cliq_base_url(self) -> str:
        return f"https://cliq.zoho.{self.zoho_data_center}/api/v2"


# ── Required-variable contracts per pipeline stage ─────────────────
#
# Each stage runner can call `validate_for("<stage>")` near the top of
# its main() to fail fast when env vars are missing — instead of
# discovering it half-way through an expensive run.
#
# Add a stage here when introducing a new runner. Keys are the names
# users will pass at the call site; values are the Settings attribute
# names that must be non-empty for that stage to run.

REQUIRED_BY_STAGE: dict[str, tuple[str, ...]] = {
    # Gmail Stage 1 — runs against Google API via service account file
    # (`key.json`), no env var dependency.
    "gmail_fetch":        (),

    # Slack Stage 1 — fetch + discovery need an admin user token (xoxp-) or bot token (xoxb-).
    "slack_fetch":        ("slack_token",),
    "slack_discovery":    ("slack_token",),

    # Jira Stage 1 — fetch + discovery need a base URL (auth validated
    # at request time: oauth_token OR pat OR email+api_token).
    "jira_fetch":         ("jira_base_url",),
    "jira_discovery":     ("jira_base_url",),

    # Stage 2 — `--skip-llm` requires nothing; LLM path requires Anthropic.
    "filter_llm":         ("anthropic_api_key",),
    "filter":             (),

    "person_registry":    (),
    "comm_graph":         (),

    # Stage 5 — LLM triple extraction via NVIDIA Build or OpenRouter.
    "triple_extractor":   (),

    "validator":          (),

    # Stage 6.5 — embeddings. Required key depends on embedding_provider;
    # validate_for() resolves the correct key at call time.
    "embed_knowledge":    (),

    "deduplicator":       (),

    # Stage 8 — unified builder uploads embeddings to Neo4j.
    "unified_builder":    ("neo4j_uri", "neo4j_password"),

    # Build step 7 — claim substrate projection to its own Neo4j instance.
    "project_claims":     ("claims_neo4j_uri", "claims_neo4j_password"),

    # Stage 9 — post-processing embeds topic names.
    # Required key depends on embedding_provider (same as embed_knowledge).
    "post_processor":     (),

    # Chronograph (separate sub-pipeline). Postgres DSN (DATABASE_URL /
    # POSTGRES_URI) is validated at runtime in make_store().
    "chronograph_ingest": ("gitlab_pat",),
    "chronograph_query":  ("openrouter_api_key",),

    # Outlook / Teams Stage 1 — all three Azure AD credentials required.
    "outlook_fetch":      ("azure_tenant_id", "azure_client_id", "azure_client_secret"),

    # Outlook / Teams discovery — same credential set as fetch.
    "outlook_discovery":  ("azure_tenant_id", "azure_client_id", "azure_client_secret"),

    # Convenience alias used by validate_for("outlook") — resolves whichever
    # naming scheme (.env uses azure_* OR ms365_*) is present at call time
    # via a special-cased branch in validate_for().
    "outlook":            (),   # resolved dynamically — see validate_for()

    # Zoho Mail + Cliq Stage 1 — OAuth self-client refresh-token flow.
    "zoho_fetch":         ("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"),
    "zoho_discovery":     ("zoho_client_id", "zoho_client_secret", "zoho_refresh_token"),
}


class MissingConfigError(RuntimeError):
    """Raised when required env vars for a stage are missing."""


class ConnectionRoutingError(RuntimeError):
    """Raised when a run's DB connection was not resolved for the org being run.

    Distinct from MissingConfigError: the config is present and the run would
    succeed — into the wrong client's database.
    """


_EMBEDDING_KEY_BY_PROVIDER: dict[str, str] = {
    "nvidia":     "nvidia_api_key",
    "openai":     "openai_api_key",
    "openrouter": "openrouter_api_key",
    "bedrock":    "bedrock_api_key",
}

_EMBEDDING_STAGES = {"embed_knowledge", "post_processor"}


def validate_for(stage: str, settings: Settings | None = None) -> None:
    """Fail fast if any required setting for `stage` is empty.

    Raises:
        KeyError: if `stage` is not a known stage name.
        MissingConfigError: if one or more required settings are empty.
    """
    if stage not in REQUIRED_BY_STAGE:
        raise KeyError(
            f"Unknown stage {stage!r}. Known stages: "
            f"{sorted(REQUIRED_BY_STAGE)}"
        )
    s = settings or get_settings()
    required: Iterable[str] = REQUIRED_BY_STAGE[stage]

    # Embedding stages need different keys depending on embedding_provider.
    if stage in _EMBEDDING_STAGES:
        key = _EMBEDDING_KEY_BY_PROVIDER.get(s.embedding_provider, "nvidia_api_key")
        required = (key,)

    missing: list[str] = []
    if stage == "triple_extractor":
        # Required key depends on the selected extraction provider.
        if s.llm_provider == "bedrock":
            if not s.bedrock_api_key:
                missing = ["BEDROCK_API_KEY"]
        elif not (s.nvidia_api_key or s.openrouter_api_key):
            missing = ["NVIDIA_API_KEY", "OPENROUTER_API_KEY"]
    elif stage == "outlook":
        # Accept either ms365_* (LOR-66 naming) or azure_* (LOR-64 naming).
        # All three must resolve to a non-empty value via the same fallback
        # logic that fetcher.py applies at runtime.
        if not s.resolved_ms365_tenant_id:
            missing.append("MS365_TENANT_ID (or AZURE_TENANT_ID)")
        if not s.resolved_ms365_client_id:
            missing.append("MS365_CLIENT_ID (or AZURE_CLIENT_ID)")
        if not s.resolved_ms365_client_secret:
            missing.append("MS365_CLIENT_SECRET (or AZURE_CLIENT_SECRET)")
    else:
        # Iterate `required` (not REQUIRED_BY_STAGE[stage]) so the
        # embedding-provider override above is honored.
        for attr in required:
            value = getattr(s, attr)
            if not value:
                missing.append(attr.upper())
    if missing:
        raise MissingConfigError(
            f"Stage {stage!r} requires non-empty env vars: "
            f"{', '.join(missing)}. Check your .env file."
        )


def assert_connection_for_org(org: str, settings: Settings | None = None) -> None:
    """Refuse to use a DB connection that wasn't resolved for `org`.

    No-op unless `REQUIRE_REGISTRY_CONNECTION` is set (see Settings). Call this
    before opening any connection that writes a client's data.

    Raises:
        ConnectionRoutingError: if the run carries no control-plane stamp, or
            the stamp names a different org than the one being built.
    """
    s = settings or get_settings()
    if not s.require_registry_connection:
        return

    stamped = (s.lore_connection_org or "").strip()
    if not stamped:
        raise ConnectionRoutingError(
            f"Refusing to connect for org {org!r}: this run carries no "
            "LORE_CONNECTION_ORG stamp, so its database connection did not come "
            "from the registry and may belong to a different client. Start the "
            "run through the ingestion control API (POST /runs), or unset "
            "REQUIRE_REGISTRY_CONNECTION for single-tenant/local use."
        )
    if stamped != org:
        raise ConnectionRoutingError(
            f"Refusing to connect: the database connection in this environment "
            f"was resolved for org {stamped!r}, but the run is building org "
            f"{org!r}. Writing would put {org!r}'s data into {stamped!r}'s "
            "database."
        )


def neo4j_credentials_for(org: str, settings: Settings | None = None) -> tuple[str, str, str]:
    """Return (uri, username, password) for `org`, guarded by the routing check.

    The single way to get Neo4j credentials for a client — route new callers
    through here so the guard can't be forgotten.
    """
    assert_connection_for_org(org, settings)
    s = settings or get_settings()
    return s.neo4j_uri, s.neo4j_username, s.neo4j_password


def claims_neo4j_credentials_for(org: str, settings: Settings | None = None) -> tuple[str, str, str]:
    """Same guard as neo4j_credentials_for, for the separate claim-substrate
    Neo4j instance (builders/project_claims.py)."""
    assert_connection_for_org(org, settings)
    s = settings or get_settings()
    return s.claims_neo4j_uri, s.claims_neo4j_username, s.claims_neo4j_password


def env_audit(settings: Settings | None = None) -> dict[str, bool]:
    """Return a {VAR_NAME: is_set} map for every Settings field.

    Useful for a `--print-config` debug command in future runners.
    Secret values are NOT returned — only whether each is populated.
    """
    s = settings or get_settings()
    out: dict[str, bool] = {}
    for name in s.model_fields:
        out[name.upper()] = bool(getattr(s, name))
    return out


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _reset_settings() -> None:
    """Force re-initialisation on next get_settings() call.

    Tests, and `runners/substrate_flow._apply_tenant_env`, which overwrites
    os.environ with a tenant's Vault-resolved credentials at daemon startup and
    needs the singleton rebuilt from them.
    """
    global _settings
    _settings = None


__all__ = [
    "Settings",
    "REQUIRED_BY_STAGE",
    "MissingConfigError",
    "validate_for",
    "env_audit",
    "get_settings",
]
