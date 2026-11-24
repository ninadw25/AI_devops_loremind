"""
core/settings.py
────────────────
Single source of truth for all configuration.

This is the ONLY module that reads environment variables.
Every other module receives settings via dependency injection.

Usage:
    from core.settings import get_settings
    s = get_settings()
    s.neo4j_uri

The module-level singleton is built once on first call to get_settings()
and reused thereafter. In tests, call _reset_settings() between test cases
if you need different env values.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Code platform credentials ──────────────────────────────────────
    gitlab_pat: str = ""
    gitlab_base_url: str = "https://gitlab.com"
    github_pat: str = ""

    # ── LLM provider ───────────────────────────────────────────────────
    # Which provider to use for chat/synthesis.
    # Adding a new provider: create infra/llm/<name>.py, register in
    # infra/llm/registry.py, extend this Literal.
    llm_provider: Literal["nvidia", "openai", "openrouter", "bedrock"] = "nvidia"
    llm_model: str = "nvidia/nemotron-3-super-120b-a12b"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.3
    llm_reasoning_budget: int = 1024  # thinking tokens; 0 = disable

    # ── Embedding provider ─────────────────────────────────────────────
    # Separate from llm_provider — Anthropic has no embedding API,
    # and you may want NVIDIA NeMo embeddings even with OpenAI for chat.
    embedding_provider: Literal["nvidia", "openai", "openrouter", "bedrock"] = "nvidia"
    embedding_model: str = "nvidia/nemotron-3-embed-1b"
    embedding_dim: int = 2048

    # ── Provider credentials ───────────────────────────────────────────
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embed_model: str = "openai/text-embedding-3-small"
    openrouter_timeout_s: float = 30.0

    # AWS Bedrock (Anthropic Claude via Converse API). Auth uses a Bedrock
    # API key (bearer token, prefix "ABSK…") — no AWS access-key pair needed.
    bedrock_api_key: str = ""
    bedrock_region: str = "ap-south-1"

    # ── Databases ──────────────────────────────────────────────────────
    neo4j_uri: str = ""
    neo4j_username: str = Field("neo4j", alias="neo4j_username")
    neo4j_password: str = ""

    # ── Memory layer ───────────────────────────────────────────────────
    # Client-side data DB (`lore_data`) — the `memory` schema holds conversation
    # threads/turns + the KG mirror; the same instance also hosts the
    # `chronograph` schema (lore-ingestion). See DB_CONSOLIDATION_PLAN.md.
    memory_postgres_uri: str = "postgresql://lore:lore_secret@localhost:5433/lore_data"
    memory_schema: str = "memory"
    memory_recent_turns: int = 10

    # ── Hybrid / per-org connection routing ───────────────────────────
    # When enabled, the target client's Neo4j is resolved per-request from the
    # elysia-rbac internal registry instead of the static NEO4J_URI above.
    # Disabled → original single-tenant behaviour (always uses NEO4J_URI).
    tenant_routing_enabled: bool = False
    rbac_url: str = ""                      # elysia-rbac base URL, e.g. http://localhost:3000
    rbac_internal_secret: str = ""          # matches elysia-rbac INTERNAL_WEBHOOK_SECRET
    rbac_timeout_s: float = 5.0
    tenant_cache_ttl_seconds: int = 300     # per-org connection cache lifetime

    # ── Auth ───────────────────────────────────────────────────────────
    # Service-to-service trust. lore-engine sits behind the elysia core-backend
    # gateway (see docs/INTEGRATION_ELYSIA.md); the gateway verifies the user's
    # EdDSA JWT and computes content scopes, then calls lore-engine with this
    # shared secret in the X-Internal-Auth header. lore-engine trusts the
    # gateway-injected identity + scopes only when this secret matches.
    #
    # Unset is FAIL-CLOSED: require_gateway refuses every request with 503.
    # These routes decode the caller's JWT without verifying its signature (the
    # gateway already did), so this secret is the only thing standing between a
    # direct caller and the whole knowledge graph. "Not configured" must mean
    # "serve nobody", not "serve anybody with a well-formed token".
    #
    # There is NO bypass. No dev flag, no environment check, no "unset means
    # permissive" path — every one of those has been removed. To run lore-engine
    # at all, set this to the gateway's shared secret.
    internal_auth_secret: str = ""

    # Deployment stage. Refuses weak/placeholder credentials when "production".
    environment: Literal["development", "staging", "production"] = "development"

    # REMOVED 2026-07-22: jwt_secret_key / token_expiry_hours / auth_salt /
    # auth_users / auth_user_orgs. They powered a public, unauthenticated
    # POST /api/auth/login that minted HS256 tokens from password hashes in the
    # environment — a second, weaker door into the same data, with a shipped
    # default salt. lore-engine now has exactly one entrance: the gateway.

    # ── gRPC server (gateway → engine transport; see docs/GRPC_TRANSPORT.md) ──
    # When enabled, lore-engine ALSO serves Query / QueryStream / Threads over
    # gRPC on grpc_host:grpc_port, alongside the HTTP API. Fail-closed at boot:
    # enabling gRPC without INTERNAL_AUTH_SECRET is rejected (the gRPC auth
    # interceptor is always fail-closed, unlike the silent HTTP dev mode).
    grpc_enabled: bool = False
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    grpc_max_workers: int = 16

    # ── App ────────────────────────────────────────────────────────────
    org: str = ""
    cors_origins: str = "*"
    port: int = 8000

    # ── Paths ──────────────────────────────────────────────────────────
    output_dir: str = ""  # if empty, defaults to <backend_root>/output
    # Directory containing {org}_jira_person_scopes.json artifacts from lore-ingestion.
    jira_person_scopes_dir: str = ""  # if empty, defaults to <backend_root>/../lore-ingestion/output/jira

    # ── Observability ──────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_disabled: bool = False

    # ── Jira retrieval ─────────────────────────────────────────────────
    # Issues older than this many days score 0.0 on recency in reranking.
    # Tune once real production query data is available.
    jira_recency_window_days: int = 90

    # ── Query pipeline v2 (unstructured: Gmail / Slack / Outlook / Teams) ──
    # See docs/QUERY_PIPELINE_V2.md. These tune the ontology-routed GraphRAG
    # path; the Jira/GitHub paths are unaffected.

    # Pre-retrieval query analysis: one small structured LLM call.
    # fast_llm_model is the model used for it; empty → reuse llm_model.
    # Reranker provider for the cross-encoder stage (after BM25).
    fast_llm_model: str = ""
    analysis_timeout_ms: int = 800
    rerank_provider: Literal["flashrank", "nvidia", "none"] = "flashrank"   # none: ablation, hybrid order only
    rerank_model: str = "ms-marco-MiniLM-L-12-v2"          # flashrank ONNX model
    nvidia_rerank_model: str = "nvidia/llama-3.2-nv-rerankqa-1b-v2"

    # Frontier / beam expansion
    vector_seed_top_k: int = 30          # ANN seeds before expansion
    frontier_beam_width: int = 30        # candidates kept per hop
    frontier_max_depth: int = 3          # traversal hops (weights past 3 are tiny)
    frontier_convergence_factor: float = 0.1   # bonus = 1 + factor*(path_count-1)

    # Ranking — hybrid fusion then cross-encoder
    rerank_candidate_limit: int = 50     # top-N into the cross-encoder
    graph_weight: float = 0.6            # α: graph score in hybrid_score
    bm25_weight: float = 0.4             # β: bm25 score in hybrid_score
    rerank_weight: float = 0.7           # w1: cross-encoder in final_score
    hybrid_weight: float = 0.3           # w2: hybrid in final_score
    context_token_budget: int = 8000     # token-budgeted final context cut

    # ── Chronograph pipeline passthrough ──────────────────────────────
    # These fields are bundled into chronograph_config for the ingestion
    # pipeline and GitLab route; they are not used by the query engine.
    chronograph_backfill_days: int = 90
    chronograph_max_file_size: int = 1024 * 1024
    embed_batch_size: int = 50
    embed_delay_s: float = 1.5

    # ── Derived properties ─────────────────────────────────────────────

    @property
    def analysis_model(self) -> str:
        """Model for the pre-retrieval query-analysis call.

        Falls back to llm_model so a deployment can run analysis and
        synthesis on the same model until a cheaper fast model is set.
        """
        return self.fast_llm_model or self.llm_model

    @property
    def jira_scopes_dir(self) -> Path:
        if self.jira_person_scopes_dir:
            return Path(self.jira_person_scopes_dir)
        return Path(__file__).parent.parent.parent / "lore-ingestion" / "output" / "jira"

    @property
    def knowledge_output_dir(self) -> Path:
        if self.output_dir:
            return Path(self.output_dir)
        return Path(__file__).parent.parent / "output"

    @property
    def chronograph_dsn(self) -> str:
        """Postgres DSN for the chronograph query store (GitLab code intelligence).

        Honors CHRONOGRAPH_DSN if set, else co-locates with the conversation
        memory store (MEMORY_POSTGRES_URI) — the chronograph schema keeps it
        namespaced away from the memory tables in the same database.
        """
        import os
        return os.environ.get("CHRONOGRAPH_DSN") or self.memory_postgres_uri

    @property
    def chronograph_config(self) -> dict:
        """Config dict consumed by the chronograph ingestion + query pipeline."""
        return {
            "openrouter_api_key": self.openrouter_api_key,
            "openrouter_base_url": self.openrouter_base_url,
            "openrouter_embed_url": f"{self.openrouter_base_url}/embeddings",
            "openrouter_chat_url": f"{self.openrouter_base_url}/chat/completions",
            "openrouter_embed_model": self.openrouter_embed_model,
            "openrouter_llm_model": self.llm_model,
            "openrouter_timeout_s": self.openrouter_timeout_s,
            # LLM provider for chronograph chat synthesis (mirror the Gmail path).
            "llm_provider": self.llm_provider,
            "bedrock_api_key": self.bedrock_api_key,
            "bedrock_region": self.bedrock_region,
            "bedrock_model": self.llm_model,
            # Embeddings for semantic code search (query-side _embed_query).
            "nvidia_api_key": self.nvidia_api_key,
            "embedding_model": self.embedding_model,
            "nvidia_base_url": self.nvidia_base_url,
            "gitlab_pat": self.gitlab_pat,
            "github_pat": self.github_pat,
            "gitlab_base_url": self.gitlab_base_url,
            "default_backfill_days": self.chronograph_backfill_days,
            "max_file_size": self.chronograph_max_file_size,
            "embed_batch_size": self.embed_batch_size,
            "embed_delay_s": self.embed_delay_s,
        }

    @model_validator(mode="after")
    def _refuse_unsafe_production_config(self) -> "Settings":
        """Fail at boot, not at request time, on a production deploy that cannot
        be secure.

        There is no bypass flag left to check — the only remaining way to run
        lore-engine open is to deploy it with no internal secret at all. That
        already 503s every request, but failing at startup turns a silent
        outage into an obvious one.
        """
        if self.environment != "production":
            return self
        if not self.internal_auth_secret:
            raise ValueError(
                "INTERNAL_AUTH_SECRET must be set when ENVIRONMENT=production — "
                "it is the only check between a direct caller and the graph."
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _reset_settings() -> None:
    """Force re-initialisation on next get_settings() call. Test use only."""
    global _settings
    _settings = None
