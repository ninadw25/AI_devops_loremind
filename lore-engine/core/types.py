"""
core/types.py
─────────────
Shared dataclasses and TypedDicts used across all layers.

Rule: these types carry data, no logic, no IO.
Domain and service layers may extend or wrap these — never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generator, Literal, Protocol, TypedDict, runtime_checkable

from pydantic import BaseModel, Field


# ── User identity ─────────────────────────────────────────────────────

@dataclass
class UserContext:
    """Derived from a validated JWT token.

    access_groups is populated from domain.rbac.user_access_groups_from_role
    and is passed directly into Neo4j Cypher WHERE clauses.

    jira_account_id is the Atlassian accountId from OAuth; used for person
    proximity reranking in Jira retrieval. None when not authenticated via
    Atlassian OAuth or when the token predates this field.
    """
    user_id: str
    org: str
    role: str = "general"
    access_groups: list[str] = field(default_factory=list)
    # Content scopes (allowedScopes) injected by core-backend Plane B; passed
    # as $allowed_scopes into the fail-closed scope gate. Empty → sees nothing.
    allowed_scopes: list[str] = field(default_factory=list)
    jira_account_id: str | None = None


# ── Query response ────────────────────────────────────────────────────

@dataclass
class Source:
    """One source citation returned with a query answer."""
    type: str
    description: str
    thread_id: str
    score: float
    sensitivity: str


# ── Cross-source retrieval chunk ─────────────────────────────────────

class SourcedChunk(TypedDict, total=False):
    """Unified result envelope returned by per-source retrieval functions.

    Every source (Jira, Gmail, Slack) returns list[SourcedChunk] so the
    synthesis stage can merge them without knowing where they came from.
    """
    source: str           # "jira" | "gmail" | "slack"
    node_id: str
    node_type: str        # "Issue" | "Decision" | "Fact" | "Topic"
    text: str             # content the LLM reads
    score: float
    scope_ids: list[str]
    citations: list[str]  # e.g. ["ENG-42"] for Jira, thread ids for email
    created_at: str       # ISO-8601 creation timestamp; used for recency reranking across all sources


# ── Knowledge graph nodes ─────────────────────────────────────────────

class KnowledgeNode(TypedDict, total=False):
    """Shape of a node returned by Neo4j vector search or graph expansion.

    All fields are optional (total=False) because different query paths
    return different subsets. Always access with .get() or check presence.
    """
    node_id: str
    type: str                    # Decision | Task | Fact | Event | Person
    description: str
    source_thread_id: str
    score: float
    sensitivity_label: str
    access_groups: list[str]
    department_tags: list[str]

    # Graph-expanded fields (present when _expanded=True)
    decided_by: list[str]
    assigned_to: list[str]
    topics: list[str]
    evidence: list[dict]
    thread_id: str               # alias for source_thread_id on some returns
    _expanded: bool

    # Person-specific
    person_id: str
    name: str
    primary_email: str
    is_internal: bool
    decisions_made: list[str]
    tasks_assigned: list[str]
    facts_stated: list[str]
    events: list[str]
    organization: str
    reports_to: str
    direct_reports: list[str]


@dataclass
class RetrievalResult:
    """Output of the retrieval stage, before context assembly."""
    primary: list[KnowledgeNode] = field(default_factory=list)
    supporting: list[KnowledgeNode] = field(default_factory=list)
    persons: list[KnowledgeNode] = field(default_factory=list)


# ── Query pipeline v2 (unstructured sources) ──────────────────────────
# See docs/QUERY_PIPELINE_V2.md. These types carry data between the
# analyze → seed → expand → rank → assemble stages and across the
# infra/rerank Protocol boundary (which is why they live in core/).

# Intents the analysis stage classifies a question into. GLOBAL is
# reserved for community-summary questions and currently degrades to
# TOPIC_EXPLORATION (logged as intent_global_unserved).
QueryIntent = Literal[
    "PERSON_LOOKUP", "TASK_LOOKUP", "DECISION_LOOKUP", "REASONING",
    "EVENT_LOOKUP", "TOPIC_EXPLORATION", "GLOBAL", "SUMMARY",
]


class TimeRange(BaseModel):
    """Absolute time window resolved from the question, used to filter
    retrieval by node.created_at. Either bound may be None (open-ended)."""
    start: str | None = None   # ISO-8601 date/datetime, inclusive
    end: str | None = None     # ISO-8601 date/datetime, inclusive


class QueryAnalysis(BaseModel):
    """Structured output of the pre-retrieval analysis LLM call.

    Validated against untrusted model output; on any validation failure
    the pipeline falls back to the keyword classifier in domain/analysis.py.
    Surface forms in persons/orgs/topics are kept as written in the
    question — entity resolution against graph nodes happens downstream.
    """
    intent: QueryIntent = "SUMMARY"
    persons: list[str] = Field(default_factory=list)
    orgs: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    time_range: TimeRange | None = None


@dataclass
class UserScopes:
    """The content scopes a caller is allowed to see, injected by
    core-backend Plane B (allowedScopes). Passed into every retrieval
    Cypher as $allowed_scopes. Empty → fail-closed (user sees nothing).

    super_admin is the legitimate content bypass: set by the gateway when the
    caller holds the `content.read_all` authority (rbac-service Plane A). When
    true, the scope gate opens for THIS request only (tenant-bounded, since
    lore-engine is per-org routed) — the fail-closed default is unchanged for
    everyone else. It is the ONLY way past the gate: there is no global flag.
    """
    allowed: list[str] = field(default_factory=list)
    super_admin: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.allowed


@dataclass
class BeamCandidate:
    """One node on a path during frontier/beam expansion.

    node_id is the stable property id (decision_id, fact_id, person_id, …),
    never Neo4j's internal integer id. path records the traversal that
    reached this node, for context assembly and debugging.
    """
    node_id: str
    originating_seed: str
    score: float
    hop: int
    labels: list[str] = field(default_factory=list)
    path: list[tuple[str, str]] = field(default_factory=list)  # (relation, node_id)
    path_count: int = 1
    convergence_bonus: float = 1.0
    # Content carried inline from the batched neighbour fetch, so ranking
    # and assembly need no extra round-trip for seen nodes.
    node_type: str = ""
    description: str = ""
    name: str = ""
    source_thread_id: str = ""
    created_at: str = ""
    scope_ids: list[str] = field(default_factory=list)  # for source labelling + debug


class RerankDoc(TypedDict):
    """Input to a RerankProvider — an id plus the text to score."""
    node_id: str
    text: str


@dataclass
class RankedDoc:
    """Output of a RerankProvider — the input id with a relevance score,
    sorted descending by score by the provider."""
    node_id: str
    score: float


# ── Infrastructure Protocols (defined here so domain/ depends only on core/) ─

class GraphOp(str, Enum):
    """Named graph operations. Domain code submits one of these;
    infra/pg_knowledge_repository translates each to its SQL form.
    Adding an operation: add a member here + one SQL block in the impl."""

    VECTOR_SEEDS      = "vector_seeds"
    ANCHOR_PERSONS    = "anchor_persons"
    ANCHOR_TOPICS     = "anchor_topics"
    ANCHOR_ORGS       = "anchor_orgs"
    NEIGHBORS_CONTENT = "neighbors_content"
    NEIGHBORS_SPINE   = "neighbors_spine"
    THREAD_SIBLINGS   = "thread_siblings"
    TOPIC_EXPANSION   = "topic_expansion"
    FIND_PERSONS      = "find_persons"
    EXPAND_PERSON     = "expand_person"
    EXPAND_PERSON_ORG = "expand_person_org"
    PERSON_DECISIONS  = "person_decisions"
    # Jira retrieval ops
    JIRA_VECTOR_SEARCH = "jira_vector_search"
    JIRA_DERIVED_FROM  = "jira_derived_from"
    JIRA_ISSUE_EXPAND  = "jira_issue_expand"
    JIRA_BY_KEY        = "jira_by_key"
    JIRA_BY_STATUS     = "jira_by_status"
    JIRA_PERSON_ISSUES = "jira_person_issues"
    JIRA_TEXT_SEARCH   = "jira_text_search"
    JIRA_USER_PROXIMITY = "jira_user_proximity"
    # Legacy v1 path (vector.py, expansion.py, person.py)
    VECTOR_SEARCH_V1  = "vector_search_v1"
    EXPAND_NODE       = "expand_node"
    EXPAND_PERSON_ORG_V1 = "expand_person_org_v1"
    PERSON_DECISIONS_V1  = "person_decisions_v1"


class KnowledgeRepository(Protocol):
    """SQL-native knowledge graph repository backed by Postgres + pgvector.

    Domain functions submit a GraphOp with typed params; the infra impl
    (PostgresKnowledgeRepository) translates each to its SQL form.
    execute_sync() is for domain functions running inside ThreadPoolExecutor
    threads — it submits the coroutine back to the event loop captured at
    repository construction time.

    available: False if the backing pool is unreachable; callers short-circuit
    and return empty results.
    """

    @property
    def available(self) -> bool: ...

    async def execute(
        self, op: "GraphOp", params: dict[str, Any]
    ) -> "list[dict[str, Any]]": ...

    def execute_sync(
        self, op: "GraphOp", params: dict[str, Any]
    ) -> "list[dict[str, Any]]": ...


class GraphRepository(Protocol):
    """Interface domain functions require from the graph database.

    infra/neo4j_client.Neo4jClient satisfies this Protocol at runtime.
    In tests, any object with these two members works as a fake.
    """

    @property
    def available(self) -> bool: ...

    def run(self, cypher: str, **params) -> "list[dict] | None": ...


class ChatProvider(Protocol):
    """LLM chat interface. Lives in core/ so domain/ (e.g. domain/analysis.py)
    can depend on it without importing infra/. infra/llm/* implementations
    satisfy it structurally; infra/llm/base.py re-exports this symbol.

    chat_stream yields (kind, text) pairs where kind is "reasoning" | "content".
    """
    name: str
    model: str

    def chat(self, messages: list[dict], *, max_tokens: int | None = None,
             temperature: float | None = None, **provider_kwargs: Any) -> str: ...

    def chat_stream(self, messages: list[dict], *, max_tokens: int | None = None,
                    temperature: float | None = None,
                    **provider_kwargs: Any) -> Generator[tuple[str, str], None, None]: ...


@runtime_checkable
class RerankProvider(Protocol):
    """Cross-encoder reranker interface the ranking stage depends on.

    Lives in core/ (not infra/) so domain/retrieval/rank.py can annotate
    against it without importing infra — same arrangement as GraphRepository.
    infra/rerank/* implementations satisfy it structurally.
    """
    name: str
    model: str

    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]: ...
