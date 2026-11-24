"""
common/output_paths.py
──────────────────────
Central registry for all output file paths used across the pipeline.

All pipeline stages import from here rather than constructing paths
inline, so folder reorganisation only ever needs to happen in one place.

Usage:
    from common.output_paths import KnowledgePaths
    paths = KnowledgePaths(org="ezobooks")                        # Gmail / legacy (no prefix)
    paths = KnowledgePaths(org="ezobooks", source="slack")        # → ezobooks_slack_triples_*.json
    paths = KnowledgePaths(org="ezobooks", source="jira")         # → ezobooks_jira_triples_*.json
    paths = KnowledgePaths(org="ezobooks", data_dir=d)            # custom root

Source namespacing keeps each source's triples / embeddings separate so the
deduplicator and builder can operate per-source without overwriting each other.
source=None (default) keeps the legacy unsuffixed filenames for backward compat
with existing Gmail artifacts.

Structure produced under data_dir/knowledge/:
    triples/      — triples_raw, triples_validated, triples_rejected, triples_deduped
    embeddings/   — knowledge_embeddings.npy + index + checkpoint files
    registry/     — person_registry, comm_graph, comm_summary, email_lookup
    legacy/       — archived old-format artifacts (knowledge_graph_*, topic_*, …)
    backups/      — timestamped .bak files
"""

from pathlib import Path


class KnowledgePaths:
    """All output/knowledge/* paths for a given org, in one place.

    Pass source="slack" / "jira" / "gmail" to get source-namespaced filenames
    (e.g. ezobooks_slack_triples_validated.json).  source=None (default) keeps
    the original unsuffixed names — used by the Gmail pipeline and any code that
    predates multi-source support.
    """

    def __init__(self, org: str, data_dir: Path = Path("output"),
                 source: str | None = None):
        self.org    = org
        self.source = source
        self.root   = data_dir / "knowledge"

        # ── Sub-directories ──────────────────────────────────────────
        self.triples_dir    = self.root / "triples"
        self.embeddings_dir = self.root / "embeddings"
        self.registry_dir   = self.root / "registry"
        self.legacy_dir     = self.root / "legacy"
        self.backups_dir    = self.root / "backups"

    # ── Internal helpers ─────────────────────────────────────────────

    def _stem(self, suffix: str) -> str:
        """Return filename stem, source-namespaced when source is set."""
        if self.source:
            return f"{self.org}_{self.source}_{suffix}"
        return f"{self.org}_{suffix}"

    # ── Triples pipeline ─────────────────────────────────────────────

    @property
    def triples_raw(self) -> Path:
        return self.triples_dir / f"{self._stem('triples_raw')}.json"

    @property
    def triples_validated(self) -> Path:
        return self.triples_dir / f"{self._stem('triples_validated')}.json"

    @property
    def triples_rejected(self) -> Path:
        return self.triples_dir / f"{self._stem('triples_rejected')}.json"

    @property
    def triples_deduped(self) -> Path:
        return self.triples_dir / f"{self._stem('triples_deduped')}.json"

    @property
    def corroborates(self) -> Path:
        """Cross-source CORROBORATES pairs emitted by the deduplicator."""
        return self.triples_dir / f"{self._stem('corroborates')}.json"

    # ── Embeddings (also source-namespaced) ──────────────────────────

    @property
    def knowledge_embeddings(self) -> Path:
        return self.embeddings_dir / f"{self._stem('knowledge_embeddings')}.npy"

    @property
    def knowledge_embedding_index(self) -> Path:
        return self.embeddings_dir / f"{self._stem('knowledge_embedding_index')}.json"

    @property
    def knowledge_embeddings_ckpt_npy(self) -> Path:
        return self.embeddings_dir / f"{self._stem('knowledge_embeddings.ckpt')}.npy"

    @property
    def knowledge_embeddings_ckpt_json(self) -> Path:
        return self.embeddings_dir / f"{self._stem('knowledge_embeddings.ckpt')}.json"

    # ── Registry / comm ──────────────────────────────────────────────

    @property
    def person_registry(self) -> Path:
        return self.registry_dir / f"{self.org}_person_registry.json"

    @property
    def email_lookup(self) -> Path:
        return self.registry_dir / f"{self.org}_email_lookup.json"

    @property
    def comm_graph(self) -> Path:
        return self.registry_dir / f"{self.org}_comm_graph.json"

    @property
    def comm_summary(self) -> Path:
        return self.registry_dir / f"{self.org}_comm_summary.json"

    # ── Helpers ──────────────────────────────────────────────────────

    def makedirs(self) -> None:
        """Create all sub-directories if they don't exist."""
        for d in (
            self.triples_dir,
            self.embeddings_dir,
            self.registry_dir,
            self.legacy_dir,
            self.backups_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


class IdentityPaths:
    """Output paths for the centralized identity resolver (cross-source).

    Consumes each source's artifact (Gmail registry, Slack/Jira scope_principals)
    and produces the unified identity map + alias table the backend loads, plus a
    human review report for unresolved / Tier-2b cases.
    """

    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org = org
        self.root = data_dir / "identity"

    @property
    def identity(self) -> Path:
        """The deliverable: persons[] + alias_table{} → Postgres person / person_alias."""
        return self.root / f"{self.org}_identity.json"

    @property
    def review(self) -> Path:
        """Unmapped buckets + Tier-2b suggestions + conflicts, for an operator."""
        return self.root / f"{self.org}_identity_review.json"

    @property
    def resolved_registry(self) -> Path:
        """Derived from identity.json into the legacy person_registry shape so the
        validator + extractor can consume centralized identity without bespoke
        loaders. Regenerated every identity run; safe to delete."""
        return self.root / f"{self.org}_resolved_registry.json"

    def makedirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


class JiraPaths:
    """Output paths used by the Jira connector."""

    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org = org
        self.root = data_dir / "jira"
        self.raw_dir = data_dir / "raw"
        self.logs_dir = self.root / "logs"

    @property
    def scope_principals(self) -> Path:
        return self.root / f"{self.org}_scope_principals.json"

    @property
    def jira_meta(self) -> Path:
        return self.root / f"{self.org}_jira_meta.json"

    def raw_events(self, run_date: str) -> Path:
        return self.raw_dir / f"{self.org}_jira_{run_date}.jsonl"

    def makedirs(self) -> None:
        for d in (self.root, self.raw_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


class SlackPaths:
    """Output paths used by the Slack connector.

    Mirrors JiraPaths so the ACL artifact (`scope_principals.json`) is
    addressed through one registry per source rather than hardcoded
    strings in discovery.py.
    """

    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org = org
        self.root = data_dir / "slack"
        self.raw_dir = data_dir / "raw"
        self.cypher_dir = data_dir / "cypher"

    @property
    def scope_principals(self) -> Path:
        return self.root / f"{self.org}_scope_principals.json"

    @property
    def user_scopes(self) -> Path:
        """Backend-ready user→scope_ids lookup (output of slack_acl_emitter)."""
        return self.root / f"{self.org}_user_scopes.json"

    @property
    def acl_cypher(self) -> Path:
        return self.cypher_dir / f"{self.org}_slack_acl.cypher"

    def makedirs(self) -> None:
        for d in (self.root, self.raw_dir, self.cypher_dir):
            d.mkdir(parents=True, exist_ok=True)


class OutlookPaths:
    """Output paths used by the Outlook + Teams connector (LOR-65 / LOR-66).

    Mirrors SlackPaths / JiraPaths so all MS365 artifacts are addressed
    through one registry rather than hardcoded path strings scattered across
    discovery.py, run.py, and the filter/extraction stages.

    Directory layout (all relative to data_dir, default "output"):

        output/
          outlook/          ← ACL artifacts, delta token checkpoint, identities
          raw/              ← raw JSONL from the fetch stage
          filtered/         ← filtered JSONL (after pipeline/filter.py)

    Backward-compatible: the two properties that existed in LOR-65
    (identities_json, teams_scopes_json) keep their names and paths.
    """

    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org      = org
        self.data_dir = Path(data_dir)          # keep a reference for raw/filtered
        self.root     = self.data_dir / "outlook"
        self._raw_dir      = self.data_dir / "raw"
        self._filtered_dir = self.data_dir / "filtered"

    # ── LOR-65 discovery artifacts (names/paths unchanged) ───────────

    @property
    def identities_json(self) -> Path:
        """MS365 user identities artifact (emit_ms365_identities output)."""
        return self.root / f"{self.org}_ms365_identities.json"

    @property
    def teams_scopes_json(self) -> Path:
        """Teams channel scope map artifact (emit_teams_scope_map output)."""
        return self.root / f"{self.org}_ms365_teams_scopes.json"

    @property
    def scope_principals(self) -> Path:
        """Standard scope_principals artifact consumed by rbac-service and the
        centralized identity resolver — mirrors JiraPaths.scope_principals and
        SlackPaths.scope_principals so elysia-backend's loader only needs to
        add 'ms365' to its source list.

        Written by emit_ms365_scope_principals() in infra/outlook/discovery.py.

        Schema (identical to Slack / Jira):
            {
                "scopes":           [{ "scope_id", "kind", "display_name" }, …],
                "principals":       [{ "principal_id", "display_name", "email",
                                       "account_type" }, …],
                "scope_principals": [{ "scope_id", "principal_id" }, …]
            }
        """
        return self.root / f"{self.org}_scope_principals.json"

    # ── LOR-66 raw fetch outputs ──────────────────────────────────────

    @property
    def raw_mail_jsonl(self) -> Path:
        """Stable (non-timestamped) path for the most recent Outlook Mail
        raw fetch output.  The filter stage reads from here.
        The fetch runner writes timestamped files via its own OutputPaths
        helper and symlinks / copies to this path after a successful run.
        """
        return self._raw_dir / f"{self.org}_outlook_mail.jsonl"

    @property
    def raw_teams_jsonl(self) -> Path:
        """Stable (non-timestamped) path for the most recent Teams raw fetch
        output.  The filter stage reads from here.
        """
        return self._raw_dir / f"{self.org}_ms_teams.jsonl"

    # ── LOR-66 filtered outputs ───────────────────────────────────────

    @property
    def filtered_mail_jsonl(self) -> Path:
        """Outlook Mail events after pipeline/filter.py noise removal."""
        return self._filtered_dir / f"{self.org}_outlook_mail_filtered.jsonl"

    @property
    def filtered_teams_jsonl(self) -> Path:
        """Teams events after pipeline/filter.py noise removal."""
        return self._filtered_dir / f"{self.org}_ms_teams_filtered.jsonl"

    # ── LOR-66 delta token checkpoint ─────────────────────────────────

    @property
    def delta_tokens(self) -> Path:
        """Canonical location for the MS365 delta token store.

        Pass this path as ``store_path`` to ``DeltaStore(org, path=...)`` to
        override the default checkpoint-directory location used by the fetcher.
        Storing the delta token file here keeps all Outlook/Teams state under
        ``output/outlook/`` rather than ``output/checkpoints/``.
        """
        return self.root / f"{self.org}_delta_tokens.json"

    # ── Helpers ───────────────────────────────────────────────────────

    def makedirs(self) -> None:
        """Create all required directories idempotently."""
        for d in (self.root, self._raw_dir, self._filtered_dir):
            d.mkdir(parents=True, exist_ok=True)


class ZohoPaths:
    """Output paths used by the Zoho Mail + Cliq connector (LOR-13).

    Mirrors OutlookPaths so Zoho artifacts are addressed through one registry
    rather than hardcoded path strings scattered across discovery.py, run.py,
    and the filter/extraction stages.

    Directory layout (all relative to data_dir, default "output"):

        output/
          zoho/      ← ACL artifacts, cursor checkpoint, identities
          raw/       ← raw JSONL from the fetch stage
          filtered/  ← filtered JSONL (after pipeline/filter.py)
    """

    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org      = org
        self.data_dir = Path(data_dir)
        self.root     = self.data_dir / "zoho"
        self._raw_dir      = self.data_dir / "raw"
        self._filtered_dir = self.data_dir / "filtered"

    # ── Discovery artifacts ────────────────────────────────────────────

    @property
    def identities_json(self) -> Path:
        """Zoho user identities artifact (emit_zoho_identities output)."""
        return self.root / f"{self.org}_zoho_identities.json"

    @property
    def cliq_scopes_json(self) -> Path:
        """Cliq channel scope map artifact (emit_cliq_scope_map output)."""
        return self.root / f"{self.org}_zoho_cliq_scopes.json"

    @property
    def scope_principals(self) -> Path:
        """Standard scope_principals artifact — mirrors JiraPaths /
        SlackPaths / OutlookPaths.scope_principals."""
        return self.root / f"{self.org}_scope_principals.json"

    # ── Raw fetch outputs ────────────────────────────────────────────

    @property
    def raw_mail_jsonl(self) -> Path:
        return self._raw_dir / f"{self.org}_zoho_mail.jsonl"

    @property
    def raw_cliq_jsonl(self) -> Path:
        return self._raw_dir / f"{self.org}_zoho_cliq.jsonl"

    # ── Filtered outputs ─────────────────────────────────────────────

    @property
    def filtered_mail_jsonl(self) -> Path:
        return self._filtered_dir / f"{self.org}_zoho_mail_filtered.jsonl"

    @property
    def filtered_cliq_jsonl(self) -> Path:
        return self._filtered_dir / f"{self.org}_zoho_cliq_filtered.jsonl"

    # ── Cursor checkpoint (incremental sync watermark) ────────────────

    @property
    def cursor_state(self) -> Path:
        """Per-account/channel incremental-sync cursor store (mirrors
        Outlook's delta_tokens). Pass to ZohoCursorStore(org, path=...)."""
        return self.root / f"{self.org}_zoho_cursors.json"

    # ── Helpers ───────────────────────────────────────────────────────

    def makedirs(self) -> None:
        for d in (self.root, self._raw_dir, self._filtered_dir):
            d.mkdir(parents=True, exist_ok=True)
