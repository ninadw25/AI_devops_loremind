"""
scripts/jira_retrieval_experiments.py
──────────────────────────────────────
Runs multiple Jira retrieval strategies against a fixed query set and
prints a comparison table so you can evaluate which approach returns
the best results.

Usage:
    # reads .env from lore-engine root automatically
    python scripts/jira_retrieval_experiments.py --org loremind

    # override Neo4j / scopes inline
    python scripts/jira_retrieval_experiments.py \\
        --org loremind \\
        --neo4j-uri bolt://localhost:7687 \\
        --scopes-file path/to/scopes.json \\
        --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

# ── path setup (must come before project imports) ─────────────────────
# Scripts live one level below the package root; add root to sys.path so
# `from core.xxx import ...` resolves correctly when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent))

from neo4j import GraphDatabase

from core.settings import get_settings, _reset_settings
from core.logging import get_logger
from core.types import SourcedChunk
from domain.retrieval.jira import retrieve_jira

log = get_logger("jira_experiments")


# ── Test fixtures ─────────────────────────────────────────────────────

QUERIES = [
    # navigational — issue key
    {"id": "nav_key",       "text": "what is the status of OPS-2?",                   "expect": "navigational"},
    {"id": "nav_key_eng",   "text": "what is happening with ENG-7?",                  "expect": "navigational"},
    # navigational — person
    {"id": "nav_person",    "text": "what is Arnav working on?",                      "expect": "navigational"},
    {"id": "nav_person2",   "text": "what is Reia Baid working on?",                  "expect": "navigational"},
    # analytical — decisions / topics
    {"id": "ana_aws",       "text": "what decisions were made about the AWS contract?","expect": "analytical"},
    {"id": "ana_hiring",    "text": "what hiring decisions were made?",                "expect": "analytical"},
    {"id": "ana_eng",       "text": "what are the open engineering issues?",           "expect": "analytical"},
    {"id": "ana_blocked",   "text": "which issues are blocked?",                       "expect": "analytical"},
    # analytical — cross-project
    {"id": "ana_infra",     "text": "what infrastructure decisions were made?",        "expect": "analytical"},
    {"id": "ana_security",  "text": "what security or compliance decisions exist?",    "expect": "analytical"},
    # edge cases
    {"id": "edge_vague",    "text": "tell me about OPS project",                      "expect": "analytical"},
    {"id": "edge_empty",    "text": "what did the team decide about the Q3 roadmap?", "expect": "analytical"},
]

# Each strategy is a dict of kwargs passed to retrieve_jira.
# "mode" controls which internal path is forced via a wrapper.
STRATEGIES = [
    {"name": "hybrid_k10",        "k": 10, "force": None},
    {"name": "hybrid_k5",         "k": 5,  "force": None},
    {"name": "hybrid_k20",        "k": 20, "force": None},
    {"name": "navigational_only", "k": 10, "force": "navigational"},
    {"name": "analytical_only",   "k": 10, "force": "analytical"},
    # Strategy 1 — pure vector baseline (no graph traversal)
    {"name": "pure_vector",       "k": 10, "force": "pure_vector"},
    # Strategy 2 — graph-first (text search on Issue descriptions, then expand)
    {"name": "graph_first",       "k": 10, "force": "graph_first"},
    # Strategy 4 — agentic heuristic (explicit routing, no fallback)
    {"name": "agentic_heuristic", "k": 10, "force": "agentic_heuristic"},
    # Strategy 6 — hybrid + rerank by status relevance
    {"name": "hybrid_rerank",     "k": 10, "force": "hybrid_rerank"},
]


# ── Retrieval wrapper that can force a path ────────────────────────────

async def _run_strategy(
    query: str,
    embedding: list[float],
    user_scopes: list[str],
    org: str,
    k: int,
    force: str | None,
    graph,
    user_account_id: str | None = None,
) -> list[SourcedChunk]:
    if force is None:
        return await retrieve_jira(query, embedding, user_scopes, org, k, graph=graph)

    from domain.retrieval import jira as _jm

    # ── Strategy 1: pure vector — Decision/Fact chunks only, no graph traversal ──
    if force == "pure_vector":
        hits = await asyncio.to_thread(_jm._vector_search, graph, embedding, user_scopes, k)
        return [_jm._knowledge_to_chunk(h) for h in hits]

    # ── Strategy 2: graph-first — text search on Issue descriptions, then expand ──
    if force == "graph_first":
        keywords = [w.strip("?.,!") for w in query.lower().split() if len(w.strip("?.,!")) > 4]
        issue_rows: list[dict] = []
        seen_ids: set[str] = set()
        for kw in keywords[:4]:
            rows = await asyncio.to_thread(_jm._issue_text_search, graph, kw, user_scopes, k)
            for row in rows:
                if row["issue_id"] not in seen_ids:
                    seen_ids.add(row["issue_id"])
                    issue_rows.append(row)
        chunks: list[SourcedChunk] = []
        for row in issue_rows[:k]:
            expanded = await asyncio.to_thread(_jm._expand_issue, graph, row["issue_id"], user_scopes)
            if expanded:
                chunks.append(_jm._issue_to_chunk(expanded, score=0.8))
        return chunks

    # ── Strategy 4: agentic heuristic — explicit routing with analytical fallback ──
    if force == "agentic_heuristic":
        chunks_ag: list[SourcedChunk] = []
        seen_ag: set[str] = set()
        ran_analytical = False

        async def _expand_ag(issue_id: str, score: float = 0.8) -> "SourcedChunk | None":
            row = await asyncio.to_thread(_jm._expand_issue, graph, issue_id, user_scopes)
            return _jm._issue_to_chunk(row, score=score) if row else None

        status = _jm._detect_status(query)
        if status:
            rows = await asyncio.to_thread(_jm._issues_by_status, graph, status, user_scopes, k)
            for row in rows:
                if row["issue_id"] in seen_ag:
                    continue
                seen_ag.add(row["issue_id"])
                c = await _expand_ag(row["issue_id"], score=0.95)
                if c:
                    chunks_ag.append(c)
        elif _jm._ISSUE_KEY_RE.search(query):
            for key in _jm._ISSUE_KEY_RE.findall(query):
                row = await asyncio.to_thread(_jm._issue_by_key, graph, key, user_scopes)
                if not row or row["issue_id"] in seen_ag:
                    continue
                seen_ag.add(row["issue_id"])
                c = await _expand_ag(row["issue_id"], score=1.0)
                if c:
                    chunks_ag.append(c)
        elif _jm._PERSON_NAME_RE.search(query):
            for name in _jm._PERSON_NAME_RE.findall(query)[:2]:
                rows = await asyncio.to_thread(_jm._person_issues, graph, name, user_scopes, k)
                for row in rows:
                    if row["issue_id"] in seen_ag:
                        continue
                    seen_ag.add(row["issue_id"])
                    c = await _expand_ag(row["issue_id"], score=0.9)
                    if c:
                        chunks_ag.append(c)
        else:
            chunks_ag = await asyncio.to_thread(_jm._analytical, graph, embedding, user_scopes, k)
            ran_analytical = True

        # Fallback: if navigational/status path returned nothing, try analytical
        if not chunks_ag and not ran_analytical:
            chunks_ag = await asyncio.to_thread(_jm._analytical, graph, embedding, user_scopes, k)

        return chunks_ag

    # ── Strategy 6: hybrid + rerank (status, recency, person proximity) ──────────
    # retrieve_jira now applies reranking internally; pass user_account_id directly.
    if force == "hybrid_rerank":
        return await retrieve_jira(
            query, embedding, user_scopes, org, k, graph=graph,
            user_account_id=user_account_id,
        )

    # ── Force navigational / analytical (original strategies) ──────────────────
    original = _jm._classify_intent
    if force == "navigational":
        _jm._classify_intent = lambda q: "navigational"
    else:
        _jm._classify_intent = lambda q: "analytical"

    result: list[SourcedChunk] = []
    try:
        result = await retrieve_jira(query, embedding, user_scopes, org, k, graph=graph)
    finally:
        _jm._classify_intent = original

    return result


# ── Config + validation ───────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compare Jira retrieval strategies against a fixed query set."
    )
    p.add_argument("--org",        required=True, help="Organisation name (e.g. loremind)")
    p.add_argument("--neo4j-uri",  default=None,  help="Override NEO4J_URI from .env")
    p.add_argument("--neo4j-user", default=None,  help="Override NEO4J_USERNAME from .env")
    p.add_argument("--neo4j-pass", default=None,  help="Override NEO4J_PASSWORD from .env")
    p.add_argument("--scopes-file",default=None,  help="Path to {org}_jira_person_scopes.json")
    p.add_argument("--output",     default=None,  help="Write full results to this JSON file")
    p.add_argument("--queries",    default=None,  nargs="+",
                   help="Run only these query IDs (e.g. nav_key ana_aws)")
    p.add_argument("--account-id", default=None,
                   help="Atlassian accountId for person proximity reranking test")
    return p.parse_args()


def _load_config(args: argparse.Namespace):
    """Merge CLI overrides on top of settings. CLI always wins."""
    import os

    if args.neo4j_uri:
        os.environ["NEO4J_URI"] = args.neo4j_uri
    if args.neo4j_user:
        os.environ["NEO4J_USERNAME"] = args.neo4j_user
    if args.neo4j_pass:
        os.environ["NEO4J_PASSWORD"] = args.neo4j_pass

    # Reset singleton so env overrides are picked up
    _reset_settings()
    return get_settings()


def _validate(settings, args: argparse.Namespace) -> None:
    errors = []
    if not settings.neo4j_uri:
        errors.append("NEO4J_URI is not set (use --neo4j-uri or set in .env)")
    provider_key = {
        "nvidia":     ("nvidia_api_key",     "NVIDIA_API_KEY"),
        "openai":     ("openai_api_key",     "OPENAI_API_KEY"),
        "openrouter": ("openrouter_api_key", "OPENROUTER_API_KEY"),
    }.get(settings.embedding_provider)
    if provider_key and not getattr(settings, provider_key[0], ""):
        errors.append(f"{provider_key[1]} is not set")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _load_scopes(args: argparse.Namespace, settings, org: str) -> list[str]:
    """Load user scopes from file or fall back to all project scopes."""
    scopes_path: Path | None = None

    if args.scopes_file:
        scopes_path = Path(args.scopes_file)
    else:
        candidate = settings.jira_scopes_dir / f"{org}_jira_person_scopes.json"
        if candidate.exists():
            scopes_path = candidate

    if scopes_path and scopes_path.exists():
        data = json.loads(scopes_path.read_bytes().decode("utf-8"))
        all_scopes: set[str] = set()
        # Primary format: {"scopes": {"accountId": ["jira:project:X", ...]}}
        if isinstance(data.get("scopes"), dict):
            for scope_list in data["scopes"].values():
                all_scopes.update(scope_list)
        # Legacy format: {"persons": [{"scope_ids": [...]}]}
        elif isinstance(data.get("persons"), list):
            for person in data["persons"]:
                all_scopes.update(person.get("scope_ids", []))
        elif isinstance(data, list):
            for person in data:
                all_scopes.update(person.get("scope_ids", []))
        if all_scopes:
            log.info("scopes_loaded", count=len(all_scopes), path=str(scopes_path))
            return sorted(all_scopes)

    # Fallback: broad project scopes from demo data
    fallback = [
        "jira:project:OPS", "jira:project:ENG",
        "jira:project:HR",  "jira:project:EDGE",
    ]
    log.warning("no_scopes_file_using_fallback", scopes=fallback)
    return fallback


# ── Output formatting ─────────────────────────────────────────────────

def _print_table(results: list[dict]) -> None:
    col_w = {"query_id": 16, "strategy": 20, "intent": 14, "chunks": 7, "top_score": 10, "has_issue": 10, "ms": 8}
    header = (
        f"{'query_id':<{col_w['query_id']}} "
        f"{'strategy':<{col_w['strategy']}} "
        f"{'intent':<{col_w['intent']}} "
        f"{'chunks':>{col_w['chunks']}} "
        f"{'top_score':>{col_w['top_score']}} "
        f"{'has_issue':<{col_w['has_issue']}} "
        f"{'ms':>{col_w['ms']}}"
    )
    sep = "-" * len(header)
    print("\n" + sep)
    print(header)
    print(sep)
    for r in results:
        intent_marker = "" if r["intent"] == r.get("expected_intent") else " !"
        print(
            f"{r['query_id']:<{col_w['query_id']}} "
            f"{r['strategy']:<{col_w['strategy']}} "
            f"{r['intent'] + intent_marker:<{col_w['intent']}} "
            f"{r['chunks']:>{col_w['chunks']}} "
            f"{r['top_score']:>{col_w['top_score']}.3f} "
            f"{'yes' if r['has_issue'] else 'no':<{col_w['has_issue']}} "
            f"{r.get('duration_ms', 0):>{col_w['ms']}.0f}"
        )
    print(sep)
    print("  ! = intent mismatch vs expected\n")


# ── Core experiment loop ──────────────────────────────────────────────

async def run_experiments(
    queries: list[dict],
    strategies: list[dict],
    user_scopes: list[str],
    org: str,
    settings,
    user_account_id: str | None = None,
) -> list[dict]:
    from infra.embeddings.registry import build_embedding_provider

    embedder = build_embedding_provider(settings)

    class _Graph:
        available = True
        def __init__(self):
            self._d = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
            )
        def run(self, cypher, **params):
            with self._d.session() as s:
                return [r.data() for r in s.run(cypher, **params)]
        def close(self):
            self._d.close()

    graph = _Graph()
    results: list[dict] = []

    try:
        for q in queries:
            log.info("embedding_query", query=q["text"][:60])
            embedding = embedder.embed(q["text"])

            for strat in strategies:
                t0 = time.perf_counter()
                chunks = await _run_strategy(
                    q["text"], embedding, user_scopes, org,
                    k=strat["k"], force=strat["force"], graph=graph,
                    user_account_id=user_account_id,
                )
                duration_ms = round((time.perf_counter() - t0) * 1000, 1)

                from domain.retrieval.jira import _classify_intent
                intent = _classify_intent(q["text"])
                top_score = max((c.get("score", 0.0) for c in chunks), default=0.0)
                has_issue = any(c.get("node_type") == "Issue" for c in chunks)

                results.append({
                    "query_id":        q["id"],
                    "query_text":      q["text"],
                    "expected_intent": q["expect"],
                    "strategy":        strat["name"],
                    "intent":          intent,
                    "chunks":          len(chunks),
                    "top_score":       round(top_score, 4),
                    "has_issue":       has_issue,
                    "duration_ms":     duration_ms,
                    "top_chunks":      [
                        {"type": c.get("node_type"), "score": round(c.get("score", 0.0), 4),
                         "text": c.get("text", "")[:120]}
                        for c in chunks[:3]
                    ],
                })
    finally:
        graph.close()

    return results


# ── Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    settings = _load_config(args)
    _validate(settings, args)

    user_scopes = _load_scopes(args, settings, args.org)

    queries = QUERIES
    if args.queries:
        queries = [q for q in QUERIES if q["id"] in args.queries]
        if not queries:
            print(f"ERROR: no matching query IDs in {args.queries}", file=sys.stderr)
            sys.exit(1)

    user_account_id: str | None = getattr(args, "account_id", None)

    print(f"\nOrg:        {args.org}")
    print(f"Scopes:     {len(user_scopes)} loaded")
    print(f"Account ID: {user_account_id or '(none — person proximity disabled)'}")
    print(f"Queries:    {len(queries)}")
    print(f"Strategies: {len(STRATEGIES)}")

    try:
        results = asyncio.run(
            run_experiments(queries, STRATEGIES, user_scopes, args.org, settings,
                            user_account_id=user_account_id)
        )
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    _print_table(results)

    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Full results written to {out}")


if __name__ == "__main__":
    main()
