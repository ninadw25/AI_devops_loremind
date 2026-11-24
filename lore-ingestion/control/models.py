"""
control/models.py
─────────────────
Request / response models for the ingestion control API.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
SourceType = Literal["gmail", "zoho_mail", "slack", "jira", "outlook"]

# `org` becomes a filename component (output paths, logs, checkpoints, lock
# files) and an S3 key prefix. Constrain it to a slug so it can never climb out
# of the data dir or collide with another tenant's artifacts.
ORG_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"

# Env keys the control plane is allowed to set for a run: per-tenant DB routing
# and per-source connector secrets. Everything else — PATH, PYTHON*, LD_*,
# *PROXY*, SSL_*/CA bundles, and LORE_CONNECTION_ORG — is refused: those
# redirect the runner's code, its TLS trust, or its tenant stamp, none of which
# a caller may choose. LORE_CONNECTION_ORG in particular is set server-side from
# `org` after the merge (control/manager.py), because it is the guard that keeps
# one tenant's mail out of another tenant's database.
ENV_ALLOWED_PREFIXES = (
    "NEO4J_", "DATABASE_URL", "POSTGRES_",
    "ZOHO_", "SLACK_", "JIRA_", "OUTLOOK_", "MS_", "GRAPH_", "GITLAB_",
    "NVIDIA_", "OPENROUTER_", "ANTHROPIC_", "OPENAI_", "EXTRACTION_MODEL",
)
ENV_DENIED_KEYS = frozenset({"LORE_CONNECTION_ORG"})


def _data_root() -> Path:
    """Root every run's data_dir must live under. LORE_DATA_ROOT when set,
    otherwise the server's own state dir — never the whole filesystem."""
    return Path(
        os.environ.get("LORE_DATA_ROOT") or os.environ.get("LORE_STATE_DIR") or "output"
    ).resolve()


class RunSpec(BaseModel):
    """What to ingest and how. The control plane resolves per-org connection
    details and credentials from the registry/vault and passes them here — this
    server stays org-agnostic and stateless.

    This model is a TRUST BOUNDARY: possession of the shared internal secret
    proves the caller is the control plane, not that its field values are safe.
    Everything that becomes a path, an env var, or a subprocess argument is
    validated here.
    """

    org: str = Field(..., min_length=1, pattern=ORG_PATTERN)
    source: SourceType = "gmail"
    trigger: Literal["manual", "schedule"] = "manual"

    # Gmail run parameters (forwarded to runners.sync).
    domain: str | None = None
    admin_email: str | None = None
    org_domains: list[str] = Field(default_factory=list)
    extra_functional: list[str] = Field(default_factory=list)

    # Google service-account key.json *content* (raw JSON). Written to a private
    # temp file for the run and deleted afterwards.
    # ponytail: the only connector credential still riding the trigger. Zoho
    # reads its own from the tenant's Vault subtree at launch; move Gmail the
    # same way (secret/tenants/{org}/gmail) when a second Gmail client lands.
    gmail_key_content: str | None = None

    # Per-run environment injection — the mechanism for per-org routing.
    # The control plane puts the client's NEO4J_URI / NEO4J_PASSWORD / source
    # tokens / API keys here; they override the server's own environment for
    # this run only.
    env: dict[str, str] = Field(default_factory=dict)

    # Overrides LORE_STATE_DIR for this run (per-org artifact dir / mount).
    data_dir: str | None = None

    # elysia-rbac's ingestion_run id, echoed back in the completion callback so
    # rbac can reconcile the run's terminal state. Set by the control plane.
    callbackRunId: str | None = None

    # ── validation ─────────────────────────────────────────────────────

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: dict[str, str]) -> dict[str, str]:
        for key in v:
            if key in ENV_DENIED_KEYS:
                raise ValueError(
                    f"env key {key!r} is set server-side and cannot be supplied per run"
                )
            if not key.startswith(ENV_ALLOWED_PREFIXES):
                raise ValueError(
                    f"env key {key!r} is not in the per-run allowlist "
                    f"(allowed prefixes: {', '.join(ENV_ALLOWED_PREFIXES)})"
                )
        return v

    @field_validator("data_dir")
    @classmethod
    def _check_data_dir(cls, v: str | None) -> str | None:
        if v is None:
            return v
        root = _data_root()
        resolved = Path(v).resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"data_dir must be inside {root} (got {resolved})")
        return str(resolved)

    @field_validator("org_domains", "extra_functional")
    @classmethod
    def _check_argv_list(cls, v: list[str]) -> list[str]:
        # These are splatted into the runner's argv after a `nargs="*"` flag, so
        # an element starting with "-" would be parsed as a new option.
        for item in v:
            if item.startswith("-"):
                raise ValueError(f"value {item!r} may not start with '-'")
        return v


class RunRecord(BaseModel):
    """Observable state of a launched run."""

    run_id: str
    org: str
    source: str
    trigger: str
    status: RunStatus
    started_at: str | None = None
    completed_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    log_path: str | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: RunStatus
