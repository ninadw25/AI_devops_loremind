"""
infra/pg_knowledge_repository.py
─────────────────────────────────
PostgresKnowledgeRepository: SQL + pgvector implementation of KnowledgeRepository.

Domain code submits a GraphOp with typed params; this module translates each
to its SQL form. Adding an operation: add a case in _dispatch() + one function.

Scope gating is fail-closed: nodes with no matching scope_ids are invisible.
All queries are org-scoped via org_id injected at construction time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import and_, cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import String

from core.logging import get_logger
from core.types import GraphOp
from infra.db.models import KnowledgeEdgeModel, KnowledgeNodeModel
from infra.postgres_client import PostgresClient

log = get_logger(__name__)

_CONTENT_TYPES = ["Decision", "Task", "Fact", "Event"]


def _arr(values: list[str]):
    """Cast a Python list to a Postgres text[] for && overlap queries."""
    return cast(literal(values, ARRAY(String)), ARRAY(String))


class PostgresKnowledgeRepository:
    """Implements KnowledgeRepository Protocol backed by Postgres + pgvector.

    Construct once per request (or per org); pass org_id at construction so
    every query is automatically scoped — callers never pass org_id in params.

    execute_sync() is for domain functions running inside ThreadPoolExecutor
    threads. Call capture_loop() once from the async context before spawning
    threads.
    """

    def __init__(self, client: PostgresClient, org_id: str) -> None:
        self._client = client
        self._org_id = org_id
        self._loop: asyncio.AbstractEventLoop | None = None
        self._available = True

    @property
    def available(self) -> bool:
        return self._available

    def capture_loop(self) -> None:
        """Capture the running event loop for execute_sync(). Call once at startup."""
        self._loop = asyncio.get_event_loop()

    async def execute(
        self, op: GraphOp, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        try:
            async with self._client.session() as session:
                return await _dispatch(session, op, self._org_id, params)
        except Exception as exc:
            log.error("pg_knowledge_op_failed", op=op.value, error=str(exc))
            raise

    def execute_sync(
        self, op: GraphOp, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("capture_loop() must be called before execute_sync()")
        fut = asyncio.run_coroutine_threadsafe(self.execute(op, params), loop)
        return fut.result(timeout=30.0)


# ── Dispatcher ────────────────────────────────────────────────────────

async def _dispatch(
    s: AsyncSession, op: GraphOp, org_id: str, p: dict[str, Any]
) -> list[dict[str, Any]]:
    match op:
        case GraphOp.VECTOR_SEEDS | GraphOp.VECTOR_SEARCH_V1:
            return await _vector_seeds(s, org_id, p)
        case GraphOp.ANCHOR_PERSONS:
            return await _anchor_persons(s, org_id, p)
        case GraphOp.ANCHOR_TOPICS:
            return await _anchor_topics(s, org_id, p)
        case GraphOp.ANCHOR_ORGS:
            return await _anchor_orgs(s, org_id, p)
        case GraphOp.NEIGHBORS_CONTENT:
            return await _neighbors_content(s, org_id, p)
        case GraphOp.NEIGHBORS_SPINE:
            return await _neighbors_spine(s, org_id, p)
        case GraphOp.THREAD_SIBLINGS:
            return await _thread_siblings(s, org_id, p)
        case GraphOp.TOPIC_EXPANSION:
            return await _topic_expansion(s, org_id, p)
        case GraphOp.FIND_PERSONS:
            return await _find_persons(s, org_id, p)
        case GraphOp.EXPAND_PERSON | GraphOp.EXPAND_PERSON_ORG | GraphOp.EXPAND_PERSON_ORG_V1:
            return await _expand_person(s, org_id, p)
        case GraphOp.PERSON_DECISIONS | GraphOp.PERSON_DECISIONS_V1:
            return await _person_decisions(s, org_id, p)
        case GraphOp.EXPAND_NODE:
            return await _expand_node(s, org_id, p)
        case GraphOp.JIRA_VECTOR_SEARCH:
            return await _jira_vector_search(s, org_id, p)
        case GraphOp.JIRA_DERIVED_FROM:
            return await _jira_derived_from(s, org_id, p)
        case GraphOp.JIRA_ISSUE_EXPAND:
            return await _jira_issue_expand(s, org_id, p)
        case GraphOp.JIRA_BY_KEY:
            return await _jira_by_key(s, org_id, p)
        case GraphOp.JIRA_BY_STATUS:
            return await _jira_by_status(s, org_id, p)
        case GraphOp.JIRA_PERSON_ISSUES:
            return await _jira_person_issues(s, org_id, p)
        case GraphOp.JIRA_TEXT_SEARCH:
            return await _jira_text_search(s, org_id, p)
        case GraphOp.JIRA_USER_PROXIMITY:
            return await _jira_user_proximity(s, org_id, p)
        case _:
            log.warning("pg_knowledge_unknown_op", op=op.value)
            return []


def _rows(result) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result]


# ── Vector ops ────────────────────────────────────────────────────────

async def _vector_seeds(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    embedding = p.get("embedding")
    if not embedding:
        return []
    allowed_scopes = p.get("allowed_scopes") or []
    user_groups = p.get("user_groups") or []
    node_types = p.get("node_types") or _CONTENT_TYPES
    k = int(p.get("k", 20))

    n = KnowledgeNodeModel
    stmt = (
        select(
            n.node_id, n.node_type, n.description, n.source_thread_id,
            n.scope_ids, n.access_groups, n.sensitivity_label,
            n.department_tags, n.created_at,
            (1 - n.embedding.cosine_distance(embedding)).label("score"),
        )
        .where(
            n.org_id == org_id,
            n.node_type.in_(node_types),
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
            n.access_groups.op("&&")(_arr(user_groups)),
            n.embedding.is_not(None),
        )
        .order_by(n.embedding.cosine_distance(embedding))
        .limit(k)
    )
    return _rows(await s.execute(stmt))


async def _person_decisions(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    """Decisions authored by a specific person, cosine-ranked against query embedding."""
    person_id = p.get("person_id") or p.get("node_id")
    embedding = p.get("embedding")
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 10))
    if not person_id:
        return []

    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel

    base = (
        select(
            n.node_id, n.node_type, n.description, n.source_thread_id,
            n.scope_ids, n.created_at, n.sensitivity_label,
        )
        .join(e, (e.src_id == n.node_id) & (e.dst_id == person_id) & (e.rel_type == "DECIDED"))
        .where(
            n.org_id == org_id,
            n.node_type == "Decision",
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .limit(k)
    )

    if embedding and p.get("rank_by_embedding", True):
        base = base.add_columns(
            (1 - n.embedding.cosine_distance(embedding)).label("score")
        ).where(n.embedding.is_not(None)).order_by(n.embedding.cosine_distance(embedding))

    return _rows(await s.execute(base))


# ── Anchor ops ────────────────────────────────────────────────────────

async def _anchor_persons(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    names: list[str] = p.get("names") or []
    if not names:
        return []
    n = KnowledgeNodeModel
    patterns = [f"%{nm}%" for nm in names]
    conditions = [func.lower(n.name).like(pat.lower()) for pat in patterns]
    stmt = (
        select(n.node_id, n.node_type, n.name, n.description, n.meta, n.source_thread_id)
        .where(n.org_id == org_id, n.node_type == "Person", func.or_(*conditions))
        .limit(20)
    )
    return _rows(await s.execute(stmt))


async def _anchor_topics(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    topics: list[str] = p.get("topics") or []
    if not topics:
        return []
    n = KnowledgeNodeModel
    patterns = [f"%{t}%" for t in topics]
    conditions = [func.lower(n.name).like(pat.lower()) for pat in patterns]
    stmt = (
        select(n.node_id, n.node_type, n.name, n.description)
        .where(n.org_id == org_id, n.node_type == "Topic", func.or_(*conditions))
        .limit(20)
    )
    return _rows(await s.execute(stmt))


async def _anchor_orgs(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    orgs: list[str] = p.get("orgs") or []
    if not orgs:
        return []
    n = KnowledgeNodeModel
    patterns = [f"%{o}%" for o in orgs]
    conditions = [func.lower(n.name).like(pat.lower()) for pat in patterns]
    stmt = (
        select(n.node_id, n.node_type, n.name, n.description)
        .where(n.org_id == org_id, n.node_type == "Organization", func.or_(*conditions))
        .limit(10)
    )
    return _rows(await s.execute(stmt))


# ── Graph traversal ops ────────────────────────────────────────────────

async def _neighbors_content(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    seed_ids: list[str] = p.get("seed_ids") or []
    allowed_scopes = p.get("allowed_scopes") or []
    if not seed_ids:
        return []

    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(
            n.node_id, n.node_type, n.description, n.source_thread_id,
            n.scope_ids, n.access_groups, n.sensitivity_label,
            n.created_at, e.rel_type, e.src_id.label("parent_id"),
        )
        .distinct()
        .join(e, (e.dst_id == n.node_id) | (e.src_id == n.node_id))
        .where(
            e.org_id == org_id,
            e.src_id.in_(seed_ids) | e.dst_id.in_(seed_ids),
            ~n.node_id.in_(seed_ids),
            n.node_type.in_(_CONTENT_TYPES),
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .limit(p.get("limit", 50))
    )
    return _rows(await s.execute(stmt))


async def _neighbors_spine(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    seed_ids: list[str] = p.get("seed_ids") or []
    if not seed_ids:
        return []

    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(
            n.node_id, n.node_type, n.name, n.description,
            e.rel_type, e.src_id.label("parent_id"),
        )
        .distinct()
        .join(e, (e.dst_id == n.node_id) | (e.src_id == n.node_id))
        .where(
            e.org_id == org_id,
            e.src_id.in_(seed_ids) | e.dst_id.in_(seed_ids),
            n.node_type.in_(["Person", "Topic", "Organization"]),
        )
        .limit(p.get("limit", 50))
    )
    return _rows(await s.execute(stmt))


async def _thread_siblings(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    thread_ids: list[str] = p.get("thread_ids") or []
    allowed_scopes = p.get("allowed_scopes") or []
    if not thread_ids:
        return []

    n = KnowledgeNodeModel
    stmt = (
        select(n.node_id, n.node_type, n.description, n.source_thread_id,
               n.scope_ids, n.created_at)
        .where(
            n.org_id == org_id,
            n.source_thread_id.in_(thread_ids),
            n.node_type.in_(_CONTENT_TYPES),
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .limit(p.get("limit", 30))
    )
    return _rows(await s.execute(stmt))


async def _topic_expansion(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    topic_ids: list[str] = p.get("topic_ids") or []
    allowed_scopes = p.get("allowed_scopes") or []
    if not topic_ids:
        return []

    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(
            n.node_id, n.node_type, n.description, n.source_thread_id,
            n.scope_ids, n.access_groups, n.created_at,
        )
        .distinct()
        .join(e, e.src_id == n.node_id)
        .where(
            e.org_id == org_id,
            e.dst_id.in_(topic_ids),
            e.rel_type == "REGARDING",
            n.node_type.in_(_CONTENT_TYPES),
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .limit(p.get("k", 20))
    )
    return _rows(await s.execute(stmt))


# ── Person ops ────────────────────────────────────────────────────────

async def _find_persons(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    q = p.get("q") or p.get("name") or ""
    if not q:
        return []
    pattern = f"%{q}%"
    n = KnowledgeNodeModel
    stmt = (
        select(n.node_id, n.node_type, n.name, n.description, n.meta, n.source_thread_id)
        .where(
            n.org_id == org_id,
            n.node_type == "Person",
            func.lower(n.name).like(pattern.lower())
            | func.lower(n.meta["primary_email"].as_string()).like(pattern.lower()),
        )
        .limit(10)
    )
    return _rows(await s.execute(stmt))


async def _expand_person(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    person_id = p.get("person_id") or p.get("node_id")
    if not person_id:
        return []

    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    n_alias = n.__table__.alias("related")
    stmt = text("""
        SELECT e.rel_type, e.src_id, e.dst_id,
               r.node_type, r.description, r.node_id AS related_id, r.name
        FROM kg_edges e
        JOIN kg_nodes r ON (
            CASE WHEN e.src_id = :pid THEN r.node_id = e.dst_id
                 ELSE r.node_id = e.src_id END
        )
        WHERE e.org_id = :org_id
          AND (e.src_id = :pid OR e.dst_id = :pid)
        LIMIT 100
    """)
    result = await s.execute(stmt, {"pid": person_id, "org_id": org_id})
    return _rows(result)


async def _expand_node(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    """Generic expand: all edges + connected nodes for a given node_id."""
    node_id = p.get("node_id")
    if not node_id:
        return []
    result = await s.execute(text("""
        SELECT e.rel_type, e.src_id, e.dst_id,
               r.node_type, r.description, r.node_id AS related_id,
               r.name, r.source_thread_id, r.scope_ids
        FROM kg_edges e
        JOIN kg_nodes r ON (
            CASE WHEN e.src_id = :nid THEN r.node_id = e.dst_id
                 ELSE r.node_id = e.src_id END
        )
        WHERE e.org_id = :org_id
          AND (e.src_id = :nid OR e.dst_id = :nid)
        LIMIT 50
    """), {"nid": node_id, "org_id": org_id})
    return _rows(result)


# ── Jira ops ──────────────────────────────────────────────────────────

async def _jira_vector_search(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    embedding = p.get("embedding")
    if not embedding:
        return []
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 20))

    n = KnowledgeNodeModel
    stmt = (
        select(
            n.node_id, n.node_type, n.description, n.source_thread_id,
            n.scope_ids, n.meta, n.created_at,
            (1 - n.embedding.cosine_distance(embedding)).label("score"),
        )
        .where(
            n.org_id == org_id,
            n.node_type == "JiraIssue",
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
            n.embedding.is_not(None),
        )
        .order_by(n.embedding.cosine_distance(embedding))
        .limit(k)
    )
    return _rows(await s.execute(stmt))


async def _jira_derived_from(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    issue_ids: list[str] = p.get("issue_ids") or []
    if not issue_ids:
        return []
    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(n.node_id, n.node_type, n.description, n.meta)
        .join(e, e.dst_id == n.node_id)
        .where(
            e.org_id == org_id,
            e.src_id.in_(issue_ids),
            e.rel_type == "DERIVED_FROM",
        )
    )
    return _rows(await s.execute(stmt))


async def _jira_issue_expand(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    issue_id = p.get("issue_id") or p.get("node_id")
    if not issue_id:
        return []
    n = KnowledgeNodeModel
    stmt = (
        select(n.node_id, n.node_type, n.description, n.meta, n.created_at, n.scope_ids)
        .where(n.org_id == org_id, n.node_id == issue_id)
    )
    return _rows(await s.execute(stmt))


async def _jira_by_key(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    key = p.get("key")
    if not key:
        return []
    n = KnowledgeNodeModel
    stmt = (
        select(n)
        .where(n.org_id == org_id, n.meta["key"].as_string() == key)
    )
    return _rows(await s.execute(stmt))


async def _jira_by_status(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    status = p.get("status")
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 20))
    if not status:
        return []
    n = KnowledgeNodeModel
    stmt = (
        select(n.node_id, n.node_type, n.description, n.meta, n.created_at, n.scope_ids)
        .where(
            n.org_id == org_id,
            n.node_type == "JiraIssue",
            n.meta["status"].as_string() == status,
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .order_by(n.created_at.desc())
        .limit(k)
    )
    return _rows(await s.execute(stmt))


async def _jira_person_issues(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    person_id = p.get("person_id")
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 20))
    if not person_id:
        return []
    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(n.node_id, n.node_type, n.description, n.meta, n.created_at, n.scope_ids)
        .join(e, e.src_id == n.node_id)
        .where(
            e.org_id == org_id,
            e.dst_id == person_id,
            e.rel_type == "ASSIGNED_TO",
            n.node_type == "JiraIssue",
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .order_by(n.created_at.desc())
        .limit(k)
    )
    return _rows(await s.execute(stmt))


async def _jira_text_search(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    q = p.get("q") or ""
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 20))
    if not q:
        return []
    result = await s.execute(text("""
        SELECT node_id, node_type, description, meta, created_at, scope_ids,
               ts_rank(to_tsvector('english', coalesce(description, '')),
                       plainto_tsquery('english', :q)) AS score
        FROM kg_nodes
        WHERE org_id = :org_id
          AND node_type = 'JiraIssue'
          AND to_tsvector('english', coalesce(description, ''))
              @@ plainto_tsquery('english', :q)
          AND scope_ids && :scopes::text[]
        ORDER BY score DESC
        LIMIT :k
    """), {"q": q, "org_id": org_id, "scopes": allowed_scopes, "k": k})
    return _rows(result)


async def _jira_user_proximity(s: AsyncSession, org_id: str, p: dict) -> list[dict]:
    jira_account_id = p.get("jira_account_id")
    allowed_scopes = p.get("allowed_scopes") or []
    k = int(p.get("k", 10))
    if not jira_account_id:
        return []
    n = KnowledgeNodeModel
    e = KnowledgeEdgeModel
    stmt = (
        select(n.node_id, n.description, n.meta, n.created_at, e.rel_type)
        .join(e, e.src_id == n.node_id)
        .where(
            e.org_id == org_id,
            e.dst_id == jira_account_id,
            n.node_type == "JiraIssue",
            n.scope_ids.op("&&")(_arr(allowed_scopes)),
        )
        .order_by(n.created_at.desc())
        .limit(k)
    )
    return _rows(await s.execute(stmt))
