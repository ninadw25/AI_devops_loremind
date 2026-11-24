"""
domain/retrieval/jira.py
─────────────────────────
Jira retrieval — hybrid strategy with intent routing.

  Navigational (issue key present / person name / status query)
      → direct Issue lookup by key or person, then neighbourhood expand

  Analytical (decisions / topics / blockers / open-ended)
      → vector search over Decision+Fact nodes
        → DERIVED_FROM back to source Issue
        → neighbourhood expand (assignee, reporter, sprint, decisions, facts)

RBAC gate is applied in every Cypher query. Nodes with scope_ids = [] or
scope_ids = NULL never appear (fail-closed by construction).

Entry point:
  retrieve_jira(query, query_embedding, user_scopes, org, k, *, graph)
      → list[SourcedChunk]
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, SourcedChunk, UserScopes
from domain.scopes import jira_source_filter, scope_gate, scope_params

log = get_logger(__name__)

# Recency-scoring window when none is injected (matches settings default).
# Domain stays env-free; QueryService passes settings.jira_recency_window_days.
_DEFAULT_RECENCY_WINDOW_DAYS = 90

# ── Intent classification ─────────────────────────────────────────────

_ISSUE_KEY_RE = re.compile(r"\b[A-Z]{2,10}-\d+\b")
_PERSON_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")

_NAVIGATIONAL_SIGNALS = (
    "status of", "what is the status", "assigned to", "who is working on",
    "who owns", "current status", "progress on", "update on",
    "show me", "find issue", "what happened to", "is blocked",
    "working on", "what is", "who is", "is assigned",
)

# Maps query keywords → canonical Jira status values.
# Checked before intent routing so "blocked" queries don't fall through
# to vector search, which can't find status field values.
_STATUS_KEYWORDS: dict[str, str] = {
    "blocked":     "Blocked",
    "in progress": "In Progress",
    "in-progress": "In Progress",
    "in_progress": "In Progress",
    "done":        "Done",
    "completed":   "Done",
    "closed":      "Done",
    "to do":       "To Do",
    "todo":        "To Do",
    "pending":     "To Do",
    "not started": "To Do",
    "open":        "To Do",
}


def _detect_status(query: str) -> str | None:
    """Return the canonical Jira status if the query is asking by status, else None."""
    q = query.lower()
    for keyword, status in _STATUS_KEYWORDS.items():
        if keyword in q:
            return status
    return None


def _classify_intent(query: str) -> str:
    if _ISSUE_KEY_RE.search(query):
        return "navigational"
    q = query.lower()
    # Person name present + work/status signal → navigational
    has_person = bool(_PERSON_NAME_RE.search(query))
    if has_person and any(sig in q for sig in _NAVIGATIONAL_SIGNALS):
        return "navigational"
    if any(sig in q for sig in _NAVIGATIONAL_SIGNALS):
        return "navigational"
    return "analytical"


# ── Cypher ────────────────────────────────────────────────────────────

# Scope gating + Jira-source isolation are owned by domain/scopes.py. The
# vector search runs over the SHARED Decision/Fact space, so it must AND in
# jira_source_filter to avoid pulling Gmail/Slack/Outlook/Teams knowledge
# (the unstructured engine owns those). Issue/Sprint/etc. are Jira-only by
# label, so their lookups need only the scope gate.
_VECTOR_SEARCH_CYPHER = f"""
CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $embedding)
YIELD node AS n, score
WHERE (n:Decision OR n:Fact)
  AND {scope_gate('n')}
  AND {jira_source_filter('n')}
RETURN
    coalesce(n.decision_id, n.fact_id) AS node_id,
    labels(n)[0]                        AS node_type,
    n.description                       AS text,
    n.scope_ids                         AS scope_ids,
    score
ORDER BY score DESC
LIMIT $limit
"""

_DERIVED_FROM_CYPHER = f"""
MATCH (n:KnowledgeNode)-[:DERIVED_FROM]->(i:Issue)
WHERE (n.decision_id = $node_id OR n.fact_id = $node_id)
  AND {scope_gate('i')}
RETURN
    i.issue_id    AS issue_id,
    i.key         AS key,
    i.description AS summary,
    i.status      AS status,
    i.issue_type  AS issue_type,
    i.scope_ids   AS scope_ids
LIMIT 1
"""

# Uses CALL subqueries to avoid Cartesian product across multiple OPTIONAL MATCHes.
_ISSUE_NEIGHBOURHOOD_CYPHER = f"""
MATCH (i:Issue {{issue_id: $issue_id}})
CALL (i) {{
    OPTIONAL MATCH (i)-[:ASSIGNED_TO]->(assignee)
    RETURN coalesce(head(collect(assignee.name)), '') AS assignee
}}
CALL (i) {{
    OPTIONAL MATCH (i)-[:REPORTED_BY]->(reporter)
    RETURN coalesce(head(collect(reporter.name)), '') AS reporter
}}
CALL (i) {{
    OPTIONAL MATCH (i)-[:IN_SPRINT]->(sprint:Sprint)
    RETURN head(collect(sprint.name)) AS sprint
}}
CALL (i) {{
    OPTIONAL MATCH (i)-[:HAS_COMPONENT]->(comp:Component)
    RETURN [x IN collect(comp.name) WHERE x IS NOT NULL] AS components
}}
CALL (i) {{
    OPTIONAL MATCH (d:Decision)-[:DERIVED_FROM]->(i)
    WHERE {scope_gate('d')}
    RETURN [x IN collect(DISTINCT d.description) WHERE x IS NOT NULL][..5] AS decisions
}}
CALL (i) {{
    OPTIONAL MATCH (f:Fact)-[:DERIVED_FROM]->(i)
    WHERE {scope_gate('f')}
    RETURN [x IN collect(DISTINCT f.description) WHERE x IS NOT NULL][..5] AS facts
}}
RETURN
    i.issue_id        AS issue_id,
    i.key             AS key,
    i.description     AS summary,
    i.status          AS status,
    i.issue_type      AS issue_type,
    i.scope_ids       AS scope_ids,
    i.updated_at_jira AS updated_at_jira,
    i.created_at_jira AS created_at_jira,
    assignee, reporter, sprint, components, decisions, facts
"""

_ISSUE_BY_KEY_CYPHER = f"""
MATCH (i:Issue {{key: $key}})
WHERE {scope_gate('i')}
RETURN
    i.issue_id    AS issue_id,
    i.key         AS key,
    i.description AS summary,
    i.status      AS status,
    i.issue_type  AS issue_type,
    i.scope_ids   AS scope_ids
"""

_ISSUES_BY_STATUS_CYPHER = f"""
MATCH (i:Issue)
WHERE toLower(i.status) = toLower($status)
  AND {scope_gate('i')}
RETURN
    i.issue_id    AS issue_id,
    i.key         AS key,
    i.description AS summary,
    i.status      AS status,
    i.issue_type  AS issue_type,
    i.scope_ids   AS scope_ids
ORDER BY i.key
LIMIT $limit
"""

_PERSON_ISSUES_CYPHER = f"""
MATCH (p)
WHERE (p:Person OR p:JiraPerson)
  AND toLower(p.name) CONTAINS toLower($name_fragment)
WITH p LIMIT 1
MATCH (i:Issue)-[:ASSIGNED_TO|REPORTED_BY]->(p)
WHERE {scope_gate('i')}
RETURN
    i.issue_id    AS issue_id,
    i.key         AS key,
    i.description AS summary,
    i.status      AS status,
    i.issue_type  AS issue_type,
    i.scope_ids   AS scope_ids,
    p.name        AS person_name
ORDER BY i.status
LIMIT $limit
"""

_ISSUE_TEXT_SEARCH_CYPHER = f"""
MATCH (i:Issue)
WHERE toLower(i.description) CONTAINS toLower($text_fragment)
  AND {scope_gate('i')}
RETURN
    i.issue_id    AS issue_id,
    i.key         AS key,
    i.description AS summary,
    i.status      AS status,
    i.issue_type  AS issue_type,
    i.scope_ids   AS scope_ids
ORDER BY i.key
LIMIT $limit
"""

_USER_PROXIMITY_CYPHER = f"""
MATCH (p:JiraPerson {{account_id: $account_id}})
WITH p
MATCH (i:Issue)-[:ASSIGNED_TO|REPORTED_BY]->(p)
WHERE {scope_gate('i')}
RETURN i.issue_id AS issue_id
LIMIT 200
"""


# ── Result assembly ───────────────────────────────────────────────────

def _issue_to_chunk(row: dict, score: float = 0.8) -> SourcedChunk:
    parts = [f"[{row.get('issue_type', 'Issue')} {row.get('key', '')}] {row.get('summary', '')}"]
    if row.get("status"):
        parts.append(f"Status: {row['status']}")
    if row.get("assignee"):
        parts.append(f"Assigned to: {row['assignee']}")
    if row.get("reporter"):
        parts.append(f"Reported by: {row['reporter']}")
    if row.get("sprint"):
        parts.append(f"Sprint: {row['sprint']}")
    if row.get("components"):
        parts.append(f"Components: {', '.join(row['components'])}")
    if row.get("decisions"):
        parts.append("Decisions: " + " | ".join(row["decisions"][:3]))
    if row.get("facts"):
        parts.append("Facts: " + " | ".join(row["facts"][:3]))
    return SourcedChunk(
        source="jira",
        node_id=row.get("issue_id", ""),
        node_type="Issue",
        text="\n".join(parts),
        score=score,
        scope_ids=row.get("scope_ids") or [],
        citations=[row["key"]] if row.get("key") else [],
        created_at=row.get("updated_at_jira") or row.get("created_at_jira") or "",
    )


def _knowledge_to_chunk(row: dict) -> SourcedChunk:
    return SourcedChunk(
        source="jira",
        node_id=row.get("node_id", ""),
        node_type=row.get("node_type", "Decision"),
        text=row.get("text", ""),
        score=float(row.get("score", 0.0)),
        scope_ids=row.get("scope_ids") or [],
        citations=[],
    )


# ── Graph operations ──────────────────────────────────────────────────

@observe(name="retrieval.jira-vector-search")
def _vector_search(
    graph: GraphRepository,
    embedding: list[float],
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[dict]:
    return graph.run(
        _VECTOR_SEARCH_CYPHER,
        k=k * 3,
        embedding=embedding,
        limit=k,
        **scope_params(UserScopes(user_scopes, super_admin=super_admin)),
    ) or []


@observe(name="retrieval.jira-derived-from")
def _traverse_to_issue(
    graph: GraphRepository,
    node_id: str,
    user_scopes: list[str],
    super_admin: bool = False,
) -> dict | None:
    rows = graph.run(_DERIVED_FROM_CYPHER, node_id=node_id,
                     **scope_params(UserScopes(user_scopes, super_admin=super_admin))) or []
    return rows[0] if rows else None


@observe(name="retrieval.jira-neighbourhood")
def _expand_issue(
    graph: GraphRepository,
    issue_id: str,
    user_scopes: list[str],
    super_admin: bool = False,
) -> dict | None:
    rows = graph.run(_ISSUE_NEIGHBOURHOOD_CYPHER, issue_id=issue_id,
                     **scope_params(UserScopes(user_scopes, super_admin=super_admin))) or []
    return rows[0] if rows else None


@observe(name="retrieval.jira-issue-by-key")
def _issue_by_key(
    graph: GraphRepository,
    key: str,
    user_scopes: list[str],
    super_admin: bool = False,
) -> dict | None:
    rows = graph.run(_ISSUE_BY_KEY_CYPHER, key=key,
                     **scope_params(UserScopes(user_scopes, super_admin=super_admin))) or []
    return rows[0] if rows else None


@observe(name="retrieval.jira-issues-by-status")
def _issues_by_status(
    graph: GraphRepository,
    status: str,
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[dict]:
    return graph.run(
        _ISSUES_BY_STATUS_CYPHER,
        status=status,
        limit=k,
        **scope_params(UserScopes(user_scopes, super_admin=super_admin)),
    ) or []


@observe(name="retrieval.jira-person-issues")
def _person_issues(
    graph: GraphRepository,
    name_fragment: str,
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[dict]:
    return graph.run(
        _PERSON_ISSUES_CYPHER,
        name_fragment=name_fragment,
        limit=k,
        **scope_params(UserScopes(user_scopes, super_admin=super_admin)),
    ) or []


@observe(name="retrieval.jira-issue-text-search")
def _issue_text_search(
    graph: GraphRepository,
    text_fragment: str,
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[dict]:
    return graph.run(
        _ISSUE_TEXT_SEARCH_CYPHER,
        text_fragment=text_fragment,
        limit=k,
        **scope_params(UserScopes(user_scopes, super_admin=super_admin)),
    ) or []


def _user_proximity_issues(
    graph: GraphRepository,
    account_id: str,
    user_scopes: list[str],
    super_admin: bool = False,
) -> list[dict]:
    """Return issue_ids for issues assigned to or reported by the given accountId."""
    return graph.run(
        _USER_PROXIMITY_CYPHER,
        account_id=account_id,
        **scope_params(UserScopes(user_scopes, super_admin=super_admin)),
    ) or []


# ── Reranking ─────────────────────────────────────────────────────────

_RERANK_STATUS_SCORES: dict[str, float] = {
    "in progress": 1.0,
    "to do":       0.9,
    "blocked":     0.8,
    "done":        0.7,
}


def rerank_chunks(
    chunks: list[SourcedChunk],
    graph: GraphRepository,
    user_scopes: list[str],
    user_account_id: str | None = None,
    recency_window_days: int = _DEFAULT_RECENCY_WINDOW_DAYS,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    """Rerank chunks by status relevance, recency, and person proximity.

    Weights: base×0.75 + status×0.10 + recency×0.05 + proximity×0.10
    Only Issue chunks are reranked — Decision/Fact vector scores are unchanged.

    recency_window_days is injected (QueryService passes settings); domain code
    reads no env. This is a Jira-local pre-rank signal — the consolidated
    cross-encoder rerank still runs over the merged pool afterwards.
    """
    proximity_ids: set[str] = set()
    if user_account_id:
        rows = _user_proximity_issues(graph, user_account_id, user_scopes, super_admin)
        proximity_ids = {r["issue_id"] for r in rows}

    now = datetime.now(timezone.utc)

    def _score(c: SourcedChunk) -> float:
        base = float(c.get("score", 0.0))
        if c.get("node_type") != "Issue":
            return base

        text = c.get("text", "").lower()
        relevance = next(
            (v for kw, v in _RERANK_STATUS_SCORES.items() if f"status: {kw}" in text),
            0.75,
        )

        recency = 0.5
        ts = c.get("created_at")
        if ts:
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days_old = max(0, (now - dt).days)
                recency = max(0.0, 1.0 - days_old / recency_window_days)
            except ValueError:
                pass

        proximity = 1.0 if c.get("node_id") in proximity_ids else 0.0

        return base * 0.75 + relevance * 0.10 + recency * 0.05 + proximity * 0.10

    return sorted(chunks, key=_score, reverse=True)


# ── Retrieval paths ───────────────────────────────────────────────────

def _status_filter(
    graph: GraphRepository,
    status: str,
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    """Direct status-field lookup — used for 'blocked/done/in-progress' queries."""
    chunks: list[SourcedChunk] = []
    seen: set[str] = set()
    for row in _issues_by_status(graph, status, user_scopes, k, super_admin):
        issue_id = row["issue_id"]
        if issue_id in seen:
            continue
        seen.add(issue_id)
        expanded = _expand_issue(graph, issue_id, user_scopes, super_admin)
        if expanded:
            chunks.append(_issue_to_chunk(expanded, score=0.95))
    return chunks


def _analytical(
    graph: GraphRepository,
    embedding: list[float],
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    """Vector search → DERIVED_FROM → Issue neighbourhood."""
    hits = _vector_search(graph, embedding, user_scopes, k, super_admin)
    chunks: list[SourcedChunk] = []
    seen_issues: set[str] = set()

    for hit in hits:
        chunks.append(_knowledge_to_chunk(hit))

        issue_row = _traverse_to_issue(graph, hit["node_id"], user_scopes, super_admin)
        if not issue_row:
            continue
        issue_id = issue_row["issue_id"]
        if issue_id in seen_issues:
            continue
        seen_issues.add(issue_id)

        expanded = _expand_issue(graph, issue_id, user_scopes, super_admin)
        if expanded:
            chunks.append(_issue_to_chunk(expanded, score=float(hit["score"]) * 0.9))

    return chunks


def _navigational(
    graph: GraphRepository,
    query: str,
    user_scopes: list[str],
    k: int,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    """Direct Issue lookup by key or person name."""
    chunks: list[SourcedChunk] = []
    seen_issues: set[str] = set()

    for key in _ISSUE_KEY_RE.findall(query):
        row = _issue_by_key(graph, key, user_scopes, super_admin)
        if not row:
            continue
        issue_id = row["issue_id"]
        if issue_id in seen_issues:
            continue
        seen_issues.add(issue_id)
        expanded = _expand_issue(graph, issue_id, user_scopes, super_admin)
        if expanded:
            chunks.append(_issue_to_chunk(expanded, score=1.0))

    if not chunks:
        for name in _PERSON_NAME_RE.findall(query)[:2]:
            for row in _person_issues(graph, name, user_scopes, k, super_admin):
                issue_id = row["issue_id"]
                if issue_id in seen_issues:
                    continue
                seen_issues.add(issue_id)
                expanded = _expand_issue(graph, issue_id, user_scopes, super_admin)
                if expanded:
                    chunks.append(_issue_to_chunk(expanded, score=0.9))

    return chunks


# ── Public API ────────────────────────────────────────────────────────

def _sync_retrieve(
    query: str,
    query_embedding: list[float],
    user_scopes: list[str],
    k: int,
    graph: GraphRepository,
    user_account_id: str | None = None,
    recency_window_days: int = _DEFAULT_RECENCY_WINDOW_DAYS,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    # Fail-closed: no scopes → nothing visible. Mirrors the scope gate (which a
    # real graph enforces) and short-circuits before any query. A content
    # super-admin (content.read_all) bypasses this: they may have no membership
    # scopes yet must still see everything.
    if not graph.available or (not user_scopes and not super_admin):
        return []

    # Status-filter path — skipped if a person name is present, because status
    # keywords like "done" appear in person queries ("what has Arnav done?")
    # and would produce the wrong results.
    has_person = bool(_PERSON_NAME_RE.search(query))
    status = None if has_person else _detect_status(query)
    if status:
        log.info("jira_retrieve", intent="status", status=status, query_preview=query[:80])
        chunks = _status_filter(graph, status, user_scopes, k, super_admin)
        log.info("jira_retrieve_done", chunks=len(chunks), intent="status")
        return rerank_chunks(chunks, graph, user_scopes, user_account_id, recency_window_days, super_admin)

    intent = _classify_intent(query)
    log.info("jira_retrieve", intent=intent, query_preview=query[:80])

    if intent == "navigational":
        chunks = _navigational(graph, query, user_scopes, k, super_admin)
        if not chunks:
            chunks = _analytical(graph, query_embedding, user_scopes, k, super_admin)
    else:
        chunks = _analytical(graph, query_embedding, user_scopes, k, super_admin)

    log.info("jira_retrieve_done", chunks=len(chunks), intent=intent)
    return rerank_chunks(chunks, graph, user_scopes, user_account_id, recency_window_days, super_admin)


# Public sync entry for the consolidated orchestrator (QueryService runs the
# Jira and unstructured engines concurrently in threads). The async
# retrieve_jira() wrapper is retained for async callers/tests.
def retrieve_jira_sync(
    query: str,
    query_embedding: list[float],
    user_scopes: list[str],
    k: int,
    graph: GraphRepository,
    user_account_id: str | None = None,
    recency_window_days: int = _DEFAULT_RECENCY_WINDOW_DAYS,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    return _sync_retrieve(query, query_embedding, user_scopes, k, graph,
                          user_account_id, recency_window_days, super_admin)


@observe(name="retrieval.jira-retrieve")
async def retrieve_jira(
    query: str,
    query_embedding: list[float],
    user_scopes: list[str],
    org: str,
    k: int = 10,
    *,
    graph: GraphRepository,
    user_account_id: str | None = None,
    super_admin: bool = False,
) -> list[SourcedChunk]:
    """Retrieve Jira knowledge nodes relevant to a query.

    Hybrid strategy with intent routing:
      navigational → Issue lookup by key or person name → neighbourhood expand
      analytical   → vector search → DERIVED_FROM → Issue neighbourhood expand

    Reranking (status relevance + recency + person proximity) is applied
    automatically. Pass user_account_id (Atlassian accountId from JWT) to
    enable person proximity signal; None disables proximity only.

    RBAC is enforced in every Cypher query. Never returns nodes where
    scope_ids is null or empty — UNLESS super_admin is set (content.read_all),
    which opens the gate for this caller (tenant-bounded, audited upstream).
    """
    return await asyncio.to_thread(
        _sync_retrieve, query, query_embedding, user_scopes, k, graph, user_account_id,
        _DEFAULT_RECENCY_WINDOW_DAYS, super_admin
    )
