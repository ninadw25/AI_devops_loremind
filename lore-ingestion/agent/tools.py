"""
agent/tools.py
──────────────
LangChain tools wrapping the Chronograph QueryEngine.

Every tool takes `project_id` as its first arg so the agent can work
across multiple ingested repos in one conversation. Tools cover:
  - Project overview and stats
  - People: authors, contributors, collaboration pairs
  - Code: symbol search, function source, file content, complexity
  - Graph: call chains, impact analysis, class structure
  - Commits: timeline, message search, hotspots, bus factor
  - Semantic: natural-language similarity search over symbols
"""

from __future__ import annotations

import json
from typing import Optional

from langchain_core.tools import tool

from chronograph.store import make_store
from chronograph.query.query_engine import QueryEngine
from common.config import get_settings

# ── Shared store / engine (lazy singleton) ─────────────────────────────

_store = None
_engine = None
_project_ids: list[str] = []


def _get_engine(project_id: str = "") -> QueryEngine:
    global _store, _engine
    if _engine is None:
        settings = get_settings()
        _store = make_store(settings)
        if not _store.connect():
            raise RuntimeError("Cannot connect to store. Check DATABASE_URL / POSTGRES_URI.")
        _engine = QueryEngine(_store, settings.model_dump())
    return _engine


def _ep(project_id: str) -> tuple[QueryEngine, str]:
    """Return (engine, resolved_project_id) — single call for every tool."""
    return _get_engine(), _rp(project_id)


def _rp(project_id: str) -> str:
    """Short alias for _resolve_project — used inline in every tool."""
    return _resolve_project(project_id)


def _resolve_project(project_id: str) -> str:
    """Fuzzy-match a short name ('toon') to a full project_id ('toon-format/toon')."""
    global _project_ids
    # Refresh cache on first call or if empty
    if not _project_ids:
        try:
            from chronograph.store.pg_store import PgStore
            from sqlalchemy import text
            store = _get_engine().store
            if isinstance(store, PgStore):
                with store._session() as s:
                    rows = s.execute(text("SELECT project_id FROM chronograph.repos")).mappings().all()
                    _project_ids = [r["project_id"] for r in rows]
        except Exception:
            pass

    if project_id in _project_ids:
        return project_id  # exact match

    q = project_id.lower()
    # Try suffix match: 'toon' matches 'toon-format/toon'
    for pid in _project_ids:
        if pid.lower() == q or pid.lower().endswith(f"/{q}") or pid.lower().startswith(q):
            return pid

    return project_id  # return as-is if no match found


# ── Project ────────────────────────────────────────────────────────────

@tool
def list_ingested_projects() -> str:
    """List all repos that have been ingested into the code-intelligence store."""
    store = _get_engine().store
    try:
        from chronograph.store.pg_store import PgStore
        from sqlalchemy import text
        if isinstance(store, PgStore):
            with store._session() as s:
                rows = s.execute(
                    text("SELECT project_id, data FROM chronograph.repos ORDER BY updated_at DESC")
                ).mappings().all()
                projects = []
                for r in rows:
                    d = r["data"] or {}
                    stats = store.get_stats(r["project_id"])
                    projects.append({
                        "project_id": r["project_id"],
                        "source": d.get("source", "?"),
                        "full_name": d.get("full_name", r["project_id"]),
                        "commits": stats.get("commits", 0),
                        "symbols": stats.get("symbols", 0),
                        "embeddings": stats.get("embeddings", 0),
                    })
                return json.dumps(projects, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def get_project_stats(project_id: str) -> str:
    """
    Get overall statistics for a project: commit count, symbol count,
    edge counts, member count, embedding count.
    Args:
        project_id: The project slug, e.g. 'isitraghav/railgun'
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_stats(project_id), indent=2)


# ── People ─────────────────────────────────────────────────────────────

@tool
def get_contributors(project_id: str) -> str:
    """
    List all commit authors for a project with commit count, additions, deletions.
    Args:
        project_id: The project slug
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_people(project_id), indent=2, default=str)


@tool
def get_bus_factor(project_id: str, min_changes: int = 5) -> str:
    """
    Find files owned by a single developer — high bus-factor risk.
    Returns files that have been changed ≥ min_changes times but only by one person.
    Args:
        project_id: The project slug
        min_changes: Minimum change count to include a file (default 5)
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_bus_factor(project_id, min_changes=min_changes), indent=2)


@tool
def get_developer_ownership(project_id: str, email: str) -> str:
    """
    Show all code symbols authored by a specific developer (by email).
    Useful for understanding what someone owns or is responsible for.
    Args:
        project_id: The project slug
        email: The developer's email address
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    try:
        edges = store.get_edges(project_id, rel_type="AUTHORED_BY")
        owned = [e for e in edges if e["target_id"] == f"dev:{email}"]
        entity_ids = list({e["source_id"] for e in owned})
        return json.dumps({
            "email": email,
            "owned_entity_count": len(entity_ids),
            "entity_ids": entity_ids[:50],
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool
def get_collaboration_pairs(project_id: str) -> str:
    """
    Find pairs of developers who frequently co-edit the same files.
    Shows collaboration strength (number of shared files).
    Args:
        project_id: The project slug
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    edges = store.get_edges(project_id, rel_type="COLLABORATES_WITH")
    pairs = [
        {
            "dev_a": e["source_id"].replace("dev:", ""),
            "dev_b": e["target_id"].replace("dev:", ""),
            "shared_files": (e.get("properties") or {}).get("shared_files", 0),
        }
        for e in edges
    ]
    pairs.sort(key=lambda x: x["shared_files"], reverse=True)
    return json.dumps(pairs[:20], indent=2)


# ── Commits & Timeline ─────────────────────────────────────────────────

@tool
def get_commit_timeline(project_id: str, bucket: str = "month") -> str:
    """
    Get commit activity over time, bucketed by day, week, or month.
    Args:
        project_id: The project slug
        bucket: 'day', 'week', or 'month' (default: 'month')
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_timeline(project_id, bucket=bucket), indent=2)


@tool
def search_commits(project_id: str, query: str) -> str:
    """
    Search commits by message text. Finds relevant commits for a topic or feature.
    Args:
        project_id: The project slug
        query: Text to search for in commit messages
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    results = store.search_commits_by_message(project_id, query, limit=20)
    return json.dumps(results, indent=2, default=str)


@tool
def get_hotspot_files(project_id: str, limit: int = 20) -> str:
    """
    Get the most frequently changed files in a project — hotspots indicate
    high activity areas or potentially unstable code.
    Args:
        project_id: The project slug
        limit: Number of files to return (default 20)
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_hotspots(project_id, limit=limit), indent=2)


@tool
def get_recent_commits(project_id: str, limit: int = 20) -> str:
    """
    Get the most recent commits for a project with author, date, message.
    Args:
        project_id: The project slug
        limit: Number of commits to return (default 20)
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    commits = store.get_commits(project_id, limit=limit)
    return json.dumps(commits, indent=2, default=str)


# ── Code Intelligence ──────────────────────────────────────────────────

@tool
def search_symbols(project_id: str, query: str, kind: Optional[str] = None) -> str:
    """
    Search for code symbols (functions, classes, methods) by name.
    Args:
        project_id: The project slug
        query: Symbol name or partial name to search
        kind: Optional filter — 'function', 'method', or 'class'
    """
    project_id = _rp(project_id)
    return json.dumps(
        _get_engine().search_symbols(project_id, query, kind=kind, limit=20),
        indent=2,
    )


@tool
def get_function_source(project_id: str, qualified_name: str) -> str:
    """
    Get the full source code of a specific function or method.
    Args:
        project_id: The project slug
        qualified_name: Fully-qualified symbol name, e.g. 'ClassName.method_name'
    """
    project_id = _rp(project_id)
    result = _get_engine().get_function_code(project_id, qualified_name)
    if not result:
        return f"Symbol '{qualified_name}' not found in {project_id}"
    return json.dumps(result, indent=2)


@tool
def get_file_content(project_id: str, file_path: str) -> str:
    """
    Get the source code and list of symbols (functions, classes) for a file.
    Args:
        project_id: The project slug
        file_path: Path to the file, e.g. 'src/auth/login.js'
    """
    project_id = _rp(project_id)
    result = _get_engine().get_file_content(project_id, file_path)
    if not result:
        return f"File '{file_path}' not found in {project_id}"
    # Truncate content to avoid overwhelming context
    content = result.get("content", "")
    if len(content) > 3000:
        content = content[:3000] + "\n... [truncated]"
    return json.dumps({
        "file_path": result["file_path"],
        "symbols": result.get("symbols", []),
        "content_preview": content,
    }, indent=2)


@tool
def get_complexity_report(project_id: str, limit: int = 15) -> str:
    """
    Get the most complex functions in the codebase ranked by cyclomatic complexity.
    High complexity → high maintenance burden and bug risk.
    Args:
        project_id: The project slug
        limit: Number of functions to return (default 15)
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().get_complexity(project_id, limit=limit), indent=2)


@tool
def trace_function_calls(project_id: str, function_name: str) -> str:
    """
    Trace a function's call graph: what it calls and what calls it.
    Useful for understanding impact of changes.
    Args:
        project_id: The project slug
        function_name: Function or method name (exact or partial qualified name)
    """
    project_id = _rp(project_id)
    return json.dumps(_get_engine().trace_function(project_id, function_name), indent=2)


@tool
def get_call_graph_edges(project_id: str, source_symbol: Optional[str] = None) -> str:
    """
    Get CALLS edges from the code graph — who calls whom.
    Args:
        project_id: The project slug
        source_symbol: Optional entity_id to filter caller, e.g. 'entity:owner/repo:file.js:funcName'
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    edges = store.get_edges(project_id, source_id=source_symbol, rel_type="CALLS")
    return json.dumps(edges[:30], indent=2)


# ── Developer Profile ─────────────────────────────────────────────────

@tool
def get_developer_profile(project_id: str, name_or_email: str) -> str:
    """
    Build a complete profile card for a developer: commits, files owned,
    symbols authored, code complexity, collaborators, and expertise areas.
    Use this whenever asked about a specific person's contribution or knowledge.
    Args:
        project_id: The project slug
        name_or_email: Developer name (partial match OK) or email address
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    engine = _get_engine()

    # ── 1. Resolve identity: find matching email(s) ───────────────────
    all_authors = store.get_commit_authors(project_id)
    query_lower = name_or_email.lower()
    matched = [
        a for a in all_authors
        if query_lower in (a.get("email") or "").lower()
        or query_lower in (a.get("name") or "").lower()
    ]
    if not matched:
        return json.dumps({"error": f"No contributor matching '{name_or_email}' in {project_id}"})

    # Merge aliases (same person, different email)
    emails = [m["email"] for m in matched]
    total_commits = sum(m.get("commit_count", 0) for m in matched)
    display_name = matched[0].get("name") or emails[0]

    # ── 2. Files solely owned (bus-factor risk) ───────────────────────
    bus = store.compute_bus_factor(project_id, min_changes=1)
    owned_files = [
        {"file": b["file_path"], "changes": b["total_changes"]}
        for b in bus
        if b.get("sole_author") in emails
    ]

    # ── 3. All files touched (any authorship) ─────────────────────────
    all_file_authors = store.get_commit_file_authors(project_id)
    touched: dict[str, int] = {}
    for row in all_file_authors:
        if row.get("author_email") in emails:
            touched[row["file_path"]] = touched.get(row["file_path"], 0) + 1
    top_touched = sorted(touched.items(), key=lambda x: x[1], reverse=True)[:15]

    # ── 4. Symbols authored (via AUTHORED_BY edges) ───────────────────
    edges = store.get_edges(project_id, rel_type="AUTHORED_BY")
    dev_ids = {f"dev:{e}" for e in emails}
    authored_entity_ids = [e["source_id"] for e in edges if e["target_id"] in dev_ids]

    # Resolve entity IDs → symbol names
    all_symbols = store.get_symbols(project_id)
    symbol_by_entity: dict[str, dict] = {}
    for s in all_symbols:
        eid = f"entity:{project_id}:{s['file_path']}:{s['qualified_name']}"
        symbol_by_entity[eid] = s

    authored_symbols = [symbol_by_entity[eid] for eid in authored_entity_ids if eid in symbol_by_entity]
    authored_by_kind: dict[str, int] = {}
    for s in authored_symbols:
        authored_by_kind[s["kind"]] = authored_by_kind.get(s["kind"], 0) + 1

    # ── 5. Complexity of authored code ────────────────────────────────
    complex_owned = sorted(
        [s for s in authored_symbols if s.get("complexity") and s["complexity"].get("cyclomatic")],
        key=lambda s: s["complexity"]["cyclomatic"],
        reverse=True,
    )[:5]

    # ── 6. Collaborators ──────────────────────────────────────────────
    collab_edges = store.get_edges(project_id, rel_type="COLLABORATES_WITH")
    collabs = []
    for e in collab_edges:
        a = e["source_id"].replace("dev:", "")
        b = e["target_id"].replace("dev:", "")
        if a in emails or b in emails:
            partner = b if a in emails else a
            collabs.append({
                "partner": partner,
                "shared_files": (e.get("properties") or {}).get("shared_files", 0),
            })
    collabs.sort(key=lambda x: x["shared_files"], reverse=True)

    # ── 7. Expertise areas (language breakdown of owned symbols) ──────
    lang_count: dict[str, int] = {}
    for s in authored_symbols:
        lang = s.get("language") or "unknown"
        lang_count[lang] = lang_count.get(lang, 0) + 1

    # ── 8. Recent commits ─────────────────────────────────────────────
    all_commits = store.get_commits(project_id, limit=500)
    dev_commits = [
        {"sha": c["sha"][:8], "title": c.get("title", "")[:80], "date": c.get("authored_date", "")[:10]}
        for c in all_commits
        if c.get("author_email") in emails
    ][:10]

    profile = {
        "name": display_name,
        "emails": emails,
        "project": project_id,
        "summary": {
            "total_commits": total_commits,
            "files_solely_owned": len(owned_files),
            "files_contributed_to": len(touched),
            "symbols_authored": len(authored_symbols),
            "symbols_by_kind": authored_by_kind,
            "languages": lang_count,
        },
        "bus_factor_risk_files": owned_files[:10],
        "top_contributed_files": [{"file": f, "changes": n} for f, n in top_touched],
        "most_complex_code_owned": [
            {
                "symbol": s["qualified_name"],
                "file": s["file_path"],
                "cyclomatic": s["complexity"]["cyclomatic"],
                "cognitive": s["complexity"].get("cognitive"),
            }
            for s in complex_owned
        ],
        "top_collaborators": collabs[:8],
        "recent_commits": dev_commits,
    }
    return json.dumps(profile, indent=2, default=str)


# ── Person-specific deep analysis ─────────────────────────────────────

def _resolve_emails(store, project_id: str, name_or_email: str) -> tuple[str, list[str]]:
    """Shared helper: fuzzy-match name/email → (display_name, [emails])."""
    all_authors = store.get_commit_authors(project_id)
    q = name_or_email.lower()
    matched = [
        a for a in all_authors
        if q in (a.get("email") or "").lower() or q in (a.get("name") or "").lower()
    ]
    if not matched:
        return ("", [])
    emails = [m["email"] for m in matched]
    name = matched[0].get("name") or emails[0]
    return (name, emails)


@tool
def get_developer_commit_timeline(project_id: str, name_or_email: str, bucket: str = "month") -> str:
    """
    Show a developer's personal commit activity over time.
    Reveals whether they are active, slowing down, or recently onboarded.
    Args:
        project_id: The project slug
        name_or_email: Developer name or email
        bucket: 'day', 'week', or 'month'
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    name, emails = _resolve_emails(store, project_id, name_or_email)
    if not emails:
        return json.dumps({"error": f"No contributor matching '{name_or_email}'"})

    all_commits = store.get_commits(project_id, limit=0)
    dev_commits = [c for c in all_commits if c.get("author_email") in emails]

    from collections import defaultdict
    buckets: dict[str, int] = defaultdict(int)
    for c in dev_commits:
        date = (c.get("authored_date") or "")[:10]
        if not date:
            continue
        if bucket == "day":
            key = date
        elif bucket == "week":
            from datetime import datetime
            try:
                d = datetime.fromisoformat(date)
                key = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
            except Exception:
                key = date[:7]
        else:
            key = date[:7]
        buckets[key] += 1

    timeline = [{"period": k, "commits": v} for k, v in sorted(buckets.items())]
    first = timeline[0]["period"] if timeline else None
    last = timeline[-1]["period"] if timeline else None
    peak = max(timeline, key=lambda x: x["commits"]) if timeline else None

    return json.dumps({
        "developer": name,
        "emails": emails,
        "total_commits": len(dev_commits),
        "active_from": first,
        "last_active": last,
        "peak_period": peak,
        "timeline": timeline,
    }, indent=2)


@tool
def get_developer_expertise_scores(project_id: str, name_or_email: str) -> str:
    """
    Compute per-file expertise scores for a developer:
    expertise = their_changes / total_file_changes (0-100%).
    High score = deep ownership; low score = peripheral contributor.
    Args:
        project_id: The project slug
        name_or_email: Developer name or email
    """
    store = _get_engine().store
    name, emails = _resolve_emails(store, project_id, name_or_email)
    if not emails:
        return json.dumps({"error": f"No contributor matching '{name_or_email}'"})

    all_rows = store.get_commit_file_authors(project_id)

    # per-file: total changes and this-dev changes
    total: dict[str, int] = {}
    dev: dict[str, int] = {}
    for row in all_rows:
        path = row["file_path"]
        total[path] = total.get(path, 0) + 1
        if row.get("author_email") in emails:
            dev[path] = dev.get(path, 0) + 1

    scores = []
    for path, dev_changes in dev.items():
        tot = total.get(path, 1)
        scores.append({
            "file": path,
            "dev_changes": dev_changes,
            "total_changes": tot,
            "expertise_pct": round(dev_changes / tot * 100, 1),
        })
    scores.sort(key=lambda x: x["expertise_pct"], reverse=True)

    deep = [s for s in scores if s["expertise_pct"] >= 80]
    partial = [s for s in scores if 30 <= s["expertise_pct"] < 80]

    return json.dumps({
        "developer": name,
        "deep_ownership": deep[:15],       # ≥80% their changes
        "significant_contributor": partial[:15],  # 30-79%
        "all_scored_files": len(scores),
    }, indent=2)


@tool
def get_developer_risk_score(project_id: str, name_or_email: str) -> str:
    """
    Composite bus-factor risk score for a developer.
    Factors: sole-owned files, average complexity of owned code,
    commit concentration (% of total project commits).
    Higher score = more dangerous single point of failure.
    Args:
        project_id: The project slug
        name_or_email: Developer name or email
    """
    store = _get_engine().store
    name, emails = _resolve_emails(store, project_id, name_or_email)
    if not emails:
        return json.dumps({"error": f"No contributor matching '{name_or_email}'"})

    # Sole-owned files
    bus = store.compute_bus_factor(project_id, min_changes=1)
    sole_files = [b for b in bus if b.get("sole_author") in emails]

    # Commit concentration
    all_authors = store.get_commit_authors(project_id)
    total_commits = sum(a.get("commit_count", 0) for a in all_authors) or 1
    dev_commits = sum(a.get("commit_count", 0) for a in all_authors if a.get("email") in emails)
    commit_pct = round(dev_commits / total_commits * 100, 1)

    # Avg complexity of authored symbols
    edges = store.get_edges(project_id, rel_type="AUTHORED_BY")
    dev_ids = {f"dev:{e}" for e in emails}
    authored_eids = {e["source_id"] for e in edges if e["target_id"] in dev_ids}
    all_syms = store.get_symbols(project_id)
    authored_syms = [
        s for s in all_syms
        if f"entity:{project_id}:{s['file_path']}:{s['qualified_name']}" in authored_eids
        and s.get("complexity") and s["complexity"].get("cyclomatic")
    ]
    avg_complexity = (
        round(sum(s["complexity"]["cyclomatic"] for s in authored_syms) / len(authored_syms), 1)
        if authored_syms else 0
    )
    high_complexity_owned = [
        s for s in authored_syms if s["complexity"]["cyclomatic"] >= 10
    ]

    # Risk score: weighted composite (0-100)
    sole_score = min(len(sole_files) * 3, 40)        # up to 40 pts
    commit_score = min(commit_pct * 0.4, 30)          # up to 30 pts
    complexity_score = min(avg_complexity * 1.5, 30)  # up to 30 pts
    risk_total = round(sole_score + commit_score + complexity_score)

    return json.dumps({
        "developer": name,
        "risk_score": risk_total,
        "risk_level": "CRITICAL" if risk_total >= 70 else "HIGH" if risk_total >= 45 else "MEDIUM" if risk_total >= 20 else "LOW",
        "breakdown": {
            "sole_owned_files": len(sole_files),
            "sole_ownership_score": sole_score,
            "commit_concentration_pct": commit_pct,
            "commit_concentration_score": round(commit_score, 1),
            "avg_complexity_of_owned_code": avg_complexity,
            "complexity_score": round(complexity_score, 1),
        },
        "high_complexity_functions_owned": len(high_complexity_owned),
        "sole_owned_files_sample": [f["file_path"] for f in sole_files[:10]],
    }, indent=2)


@tool
def compare_developers(project_id: str, dev_a: str, dev_b: str) -> str:
    """
    Side-by-side comparison of two developers across all key metrics:
    commits, files owned, expertise depth, complexity, collaboration, risk.
    Args:
        project_id: The project slug
        dev_a: First developer name or email
        dev_b: Second developer name or email
    """
    project_id = _rp(project_id)
    store = _get_engine().store

    def _profile(name_or_email: str) -> dict:
        name, emails = _resolve_emails(store, project_id, name_or_email)
        if not emails:
            return {"error": f"Not found: {name_or_email}"}

        all_authors = store.get_commit_authors(project_id)
        commits = sum(a.get("commit_count", 0) for a in all_authors if a.get("email") in emails)

        bus = store.compute_bus_factor(project_id, min_changes=1)
        sole = [b for b in bus if b.get("sole_author") in emails]

        rows = store.get_commit_file_authors(project_id)
        touched = len({r["file_path"] for r in rows if r.get("author_email") in emails})

        edges = store.get_edges(project_id, rel_type="AUTHORED_BY")
        dev_ids = {f"dev:{e}" for e in emails}
        authored_eids = {e["source_id"] for e in edges if e["target_id"] in dev_ids}
        syms = store.get_symbols(project_id)
        authored = [
            s for s in syms
            if f"entity:{project_id}:{s['file_path']}:{s['qualified_name']}" in authored_eids
        ]
        complex_syms = [
            s for s in authored
            if s.get("complexity") and s["complexity"].get("cyclomatic")
        ]
        avg_cx = (
            round(sum(s["complexity"]["cyclomatic"] for s in complex_syms) / len(complex_syms), 1)
            if complex_syms else 0
        )
        langs = {}
        for s in authored:
            l = s.get("language") or "?"
            langs[l] = langs.get(l, 0) + 1

        collab_edges = store.get_edges(project_id, rel_type="COLLABORATES_WITH")
        collab_count = sum(
            1 for e in collab_edges
            if e["source_id"].replace("dev:", "") in emails
            or e["target_id"].replace("dev:", "") in emails
        )

        return {
            "name": name,
            "emails": emails,
            "commits": commits,
            "files_touched": touched,
            "sole_owned_files": len(sole),
            "symbols_authored": len(authored),
            "avg_complexity": avg_cx,
            "languages": langs,
            "collaborator_count": collab_count,
        }

    a = _profile(dev_a)
    b = _profile(dev_b)

    def _winner(key, higher_is_better=True):
        va, vb = a.get(key, 0), b.get(key, 0)
        if va == vb:
            return "tie"
        if higher_is_better:
            return a["name"] if va > vb else b["name"]
        return a["name"] if va < vb else b["name"]

    return json.dumps({
        "comparison": {a.get("name", dev_a): a, b.get("name", dev_b): b},
        "verdict": {
            "more_commits": _winner("commits"),
            "broader_codebase_coverage": _winner("files_touched"),
            "more_sole_ownership_risk": _winner("sole_owned_files"),
            "more_symbols_authored": _winner("symbols_authored"),
            "higher_avg_complexity": _winner("avg_complexity"),
            "more_collaborative": _winner("collaborator_count"),
        }
    }, indent=2)


# ── Developer persona report ───────────────────────────────────────────

@tool
def build_developer_persona(project_id: str, name_or_email: str) -> str:
    """
    Build a comprehensive developer persona from ALL available signals:
    commits, code ownership, complexity, PR authorship, review behaviour,
    issue reporting, collaboration network, expertise depth, activity trend,
    and communication patterns. Use this for deep research on a person.
    Returns a structured JSON report the agent should narrate in full.
    Args:
        project_id: The project slug
        name_or_email: Developer name, GitHub login, or email (partial match OK)
    """
    project_id = _rp(project_id)
    store = _get_engine().store

    # ── 1. Identity resolution ─────────────────────────────────────────
    all_authors = store.get_commit_authors(project_id)
    q = name_or_email.lower()
    matched_authors = [
        a for a in all_authors
        if q in (a.get("email") or "").lower() or q in (a.get("name") or "").lower()
    ]

    # Also try GitHub login match in PRs
    logins: list[str] = []
    emails: list[str] = []
    display_name = name_or_email
    if matched_authors:
        emails = [a["email"] for a in matched_authors]
        display_name = matched_authors[0].get("name") or emails[0]
        # Infer GitHub login from email (best-effort: use local part)
        logins = list({e.split("@")[0] for e in emails})

    # Search PRs/issues by login too
    try:
        from chronograph.store.pg_store import PgStore
        from sqlalchemy import text as _t
        if isinstance(store, PgStore):
            with store._session() as s:
                # Find logins that match the query in pull_requests or issues
                rows = s.execute(_t("""
                    SELECT DISTINCT author_login FROM chronograph.pull_requests
                    WHERE project_id = :pid AND author_login ILIKE :q
                    UNION
                    SELECT DISTINCT author_login FROM chronograph.issues
                    WHERE project_id = :pid AND author_login ILIKE :q
                """), {"pid": project_id, "q": f"%{q}%"}).fetchall()
                logins += [r[0] for r in rows if r[0] and r[0] not in logins]
    except Exception:
        pass

    if not matched_authors and not logins:
        return json.dumps({"error": f"No contributor matching '{name_or_email}' in {project_id}"})

    total_commits = sum(a.get("commit_count", 0) for a in matched_authors)
    total_repo_commits = sum(a.get("commit_count", 0) for a in all_authors) or 1
    commit_pct = round(total_commits / total_repo_commits * 100, 1)

    # ── 2. File ownership — SQL-filtered, no full-table Python scan ────
    from chronograph.store.pg_store import PgStore
    is_pg = isinstance(store, PgStore)

    touched = store.get_file_touch_counts(project_id, emails) if is_pg else {}
    total_by_file = store.get_total_touches_per_file(project_id) if is_pg else {}
    top_files = sorted(touched.items(), key=lambda x: x[1], reverse=True)[:10]

    bus = store.compute_bus_factor(project_id, min_changes=1)
    sole_files = [b for b in bus if b.get("sole_author") in emails]

    # ── 3. Authored symbols — SQL edge lookup ──────────────────────────
    authored_eids = store.get_authored_entity_ids(project_id, emails) if is_pg else set()
    all_syms = store.get_symbols(project_id)
    authored_syms = [
        s for s in all_syms
        if f"entity:{project_id}:{s['file_path']}:{s['qualified_name']}" in authored_eids
    ]
    langs: dict[str, int] = {}
    for s in authored_syms:
        langs[s.get("language") or "?"] = langs.get(s.get("language") or "?", 0) + 1

    complex_syms = sorted(
        [s for s in authored_syms if s.get("complexity") and s["complexity"].get("cyclomatic")],
        key=lambda s: s["complexity"]["cyclomatic"], reverse=True,
    )[:5]

    # ── 4. PR activity — SQL-filtered ──────────────────────────────────
    pr_data = {"authored": [], "reviewed": []}
    if is_pg:
        try:
            pr_data = store.get_developer_pr_activity(project_id, logins)
        except Exception:
            pass

    authored_prs = pr_data.get("authored", [])
    reviewed_prs = pr_data.get("reviewed", [])
    merged_prs = [p for p in authored_prs if p.get("is_merged")]
    merge_rate = round(len(merged_prs) / len(authored_prs) * 100) if authored_prs else 0
    label_count: dict[str, int] = {}
    for pr in authored_prs:
        for lbl in (pr.get("labels") or []):
            label_count[lbl] = label_count.get(lbl, 0) + 1
    review_states: dict[str, int] = {}
    for rv in reviewed_prs:
        st = rv.get("review_state", "COMMENTED")
        review_states[st] = review_states.get(st, 0) + 1

    # ── 5. Issues — SQL-filtered ───────────────────────────────────────
    issues = []
    if is_pg:
        try:
            issues = store.get_developer_issue_activity(project_id, logins)
        except Exception:
            pass
    open_issues = [i for i in issues if i.get("state") == "open"]
    closed_issues = [i for i in issues if i.get("state") == "closed"]
    issue_label_count: dict[str, int] = {}
    for i in issues:
        for lbl in (i.get("labels") or []):
            issue_label_count[lbl] = issue_label_count.get(lbl, 0) + 1

    # ── 6. Collaboration — SQL-filtered ────────────────────────────────
    collabs = store.get_collab_partners(project_id, emails) if is_pg else []

    # ── 7–8. Commit messages + timeline — SQL-filtered ─────────────────
    from collections import defaultdict
    dev_commits = store.get_commits_by_emails(project_id, emails) if is_pg else []
    msg_prefixes: dict[str, int] = {}
    monthly: dict[str, int] = defaultdict(int)
    for c in dev_commits:
        msg = (c.get("message") or "").lower().strip()
        for prefix in ("feat", "fix", "docs", "refactor", "test", "chore", "style", "perf"):
            if msg.startswith(prefix):
                msg_prefixes[prefix] = msg_prefixes.get(prefix, 0) + 1
                break
        else:
            msg_prefixes["other"] = msg_prefixes.get("other", 0) + 1
        date = (c.get("authored_date") or "")[:7]
        if date:
            monthly[date] += 1
    timeline = [{"period": k, "commits": v} for k, v in sorted(monthly.items())[-12:]]

    # Full commit messages (what they actually did) — last 15
    recent_commit_details = [
        {
            "sha": c.get("sha", "")[:8],
            "date": (c.get("authored_date") or "")[:10],
            "message": (c.get("message") or "").strip()[:400],
        }
        for c in dev_commits[:15]
    ]

    # ── 9. Expertise scores ────────────────────────────────────────────
    expertise = []
    for path, dev_changes in list(touched.items())[:20]:
        tot = total_by_file.get(path, 1)
        expertise.append({"file": path, "ownership_pct": round(dev_changes/tot*100,1), "changes": dev_changes})
    expertise.sort(key=lambda x: x["ownership_pct"], reverse=True)
    deep_expertise = [e for e in expertise if e["ownership_pct"] >= 60][:8]

    # ── 10. Risk score ─────────────────────────────────────────────────
    avg_cx = (
        round(sum(s["complexity"]["cyclomatic"] for s in complex_syms) / len(complex_syms), 1)
        if complex_syms else 0
    )
    risk_score = min(
        round(min(len(sole_files)*3, 40) + min(commit_pct*0.4, 30) + min(avg_cx*1.5, 30)),
        100
    )

    # ── 11. Work narrative — what they actually built/fixed/wrote ────────
    # PR descriptions tell intent; commit messages tell what; symbols tell how
    pr_narratives = [
        {
            "pr": f"#{p['pr_number']} {p.get('title','')}",
            "state": p.get("state"),
            "description": (p.get("body") or "").strip()[:500],
            "labels": p.get("labels", []),
        }
        for p in authored_prs[:8]
        if p.get("body") or p.get("title")
    ]
    issue_narratives = [
        {
            "issue": f"#{i['issue_number']} {i.get('title','')}",
            "state": i.get("state"),
            "description": (i.get("body") or "").strip()[:300],
            "labels": i.get("labels", []),
        }
        for i in issues[:6]
        if i.get("body") or i.get("title")
    ]
    # Key symbols they authored with signatures (shows coding patterns)
    symbol_signatures = [
        {
            "symbol": s["qualified_name"],
            "kind": s["kind"],
            "file": s["file_path"],
            "signature": s.get("signature", ""),
            "calls": (s.get("calls") or [])[:5],
        }
        for s in authored_syms
        if s.get("kind") in ("function", "method") and s.get("signature")
    ][:12]

    persona = {
        "identity": {
            "display_name": display_name,
            "emails": emails,
            "github_logins": logins,
            "project": project_id,
        },
        "contribution_summary": {
            "total_commits": total_commits,
            "commit_share_pct": commit_pct,
            "prs_authored": len(authored_prs),
            "prs_merged": len(merged_prs),
            "pr_merge_rate_pct": merge_rate,
            "prs_reviewed": len(reviewed_prs),
            "issues_opened": len(issues),
            "issues_closed": len(closed_issues),
            "files_solely_owned": len(sole_files),
            "files_contributed_to": len(touched),
            "symbols_authored": len(authored_syms),
        },
        "coding_dna": {
            "languages": langs,
            "commit_type_breakdown": msg_prefixes,
            "avg_complexity_of_owned_code": avg_cx,
            "most_complex_functions_owned": [
                {"symbol": s["qualified_name"], "file": s["file_path"],
                 "cyclomatic": s["complexity"]["cyclomatic"],
                 "cognitive": s["complexity"].get("cognitive")}
                for s in complex_syms
            ],
            "deep_expertise_files": deep_expertise,
            "top_active_files": [{"file": f, "changes": n} for f, n in top_files],
        },
        "pr_and_review_behaviour": {
            "pr_labels_used": label_count,
            "review_style": review_states,
            "reviews_given_count": len(reviewed_prs),
            "reviewed_prs_sample": [
                {"pr": r.get("pr_number"), "title": r.get("title"), "state": r.get("review_state")}
                for r in reviewed_prs[:5]
            ],
        },
        "issue_reporting": {
            "total_issues_opened": len(issues),
            "open": len(open_issues),
            "closed": len(closed_issues),
            "issue_labels_used": issue_label_count,
            "recent_issues": issues[:5],
        },
        "collaboration_network": {
            "top_co_authors": collabs[:8],
        },
        "activity_timeline": timeline,
        "what_they_actually_did": {
            "recent_commits": recent_commit_details,
            "pull_requests_they_opened": pr_narratives,
            "issues_they_reported": issue_narratives,
            "key_functions_they_wrote": symbol_signatures,
        },
        "risk_assessment": {
            "risk_score": risk_score,
            "risk_level": "CRITICAL" if risk_score>=70 else "HIGH" if risk_score>=45 else "MEDIUM" if risk_score>=20 else "LOW",
            "sole_owned_files_count": len(sole_files),
            "sole_owned_files_sample": [f["file_path"] for f in sole_files[:8]],
            "commit_concentration_pct": commit_pct,
        },
    }
    # ── Pre-computed analysis — put FIRST so it's never truncated ────────
    all_files = [f for f, _ in top_files]
    role = _infer_role(all_files, list(sole_files))
    work_desc = _describe_work(recent_commit_details, pr_narratives, authored_syms[:5])
    departure_risk_str = _departure_risk(sole_files, commit_pct, risk_score)

    analysis = {
        "VERDICT": (
            f"{display_name} is a {role['title']}. "
            f"{role['one_liner'].capitalize()}. "
            f"Commit share {commit_pct}% of project."
        ),
        "INFERRED_ROLE": role["title"],
        "ROLE_EVIDENCE": role["evidence"],
        "WHAT_THEY_BUILT": work_desc,
        "DEPARTURE_IMPACT": departure_risk_str,
        "RISK_LEVEL": f"{risk_score}/100 — " + ("CRITICAL" if risk_score>=70 else "HIGH" if risk_score>=45 else "MEDIUM" if risk_score>=20 else "LOW"),
    }

    # Analysis first, supporting data after — ensures model reads conclusions before raw JSON
    output = {
        "analysis": analysis,
        "identity": persona["identity"],
        "contribution_summary": persona["contribution_summary"],
        "coding_dna": persona["coding_dna"],
        "what_they_actually_did": persona.get("what_they_actually_did", {}),
        "pr_and_review_behaviour": persona.get("pr_and_review_behaviour", {}),
        "issue_reporting": persona.get("issue_reporting", {}),
        "collaboration_network": persona.get("collaboration_network", {}),
        "activity_timeline": persona.get("activity_timeline", []),
        "risk_assessment": persona.get("risk_assessment", {}),
    }

    out = json.dumps(output, indent=2, default=str)
    if len(out) > 4000:
        out = out[:4000] + "\n... [truncated]"
    return out


def _infer_role(files: list[str], sole: list) -> dict:
    """Infer engineering role from file patterns."""
    f = " ".join(files + [s.get("file_path","") if isinstance(s,dict) else str(s) for s in sole]).lower()
    patterns = [
        (["workflows", ".github", "dockerfile", "ci.yml", "deploy.yml", "release.yml", "commitlint"],
         "DevOps / Infrastructure Engineer",
         "owns the CI/CD pipeline, automated releases, and linting infrastructure"),
        (["parser", "decoder", "lexer", "spec", "encode", "decode"],
         "Protocol / Language Implementer",
         "builds the core parsing and encoding logic"),
        (["test", "spec", "__tests__", "jest", "vitest"],
         "Quality / Test Engineer",
         "focuses on test coverage and reliability"),
        (["docs", "readme", "vitepress", "mkdocs", ".md"],
         "Developer Experience / Documentation Engineer",
         "owns documentation and developer-facing content"),
        (["auth", "session", "token", "oauth", "permission"],
         "Security Engineer",
         "owns authentication and authorization code"),
        (["migration", "schema", "model", "database", "db/"],
         "Data / Backend Engineer",
         "owns the data layer and schema"),
        (["benchmark", "perf", "profil"],
         "Performance Engineer",
         "focuses on benchmarking and optimization"),
    ]
    for keywords, title, one_liner in patterns:
        if any(k in f for k in keywords):
            matched = [k for k in keywords if k in f]
            return {"title": title, "one_liner": one_liner, "evidence": f"files: {', '.join(matched[:3])}"}
    return {"title": "General Contributor", "one_liner": "broad contributions across the codebase", "evidence": f"{len(files)} files touched"}


def _describe_work(commits: list[dict], prs: list[dict], syms: list) -> str:
    """Summarise actual work from commit messages and PR descriptions."""
    parts = []
    if commits:
        msgs = [c.get("message","").split("\n")[0][:80] for c in commits[:6] if c.get("message")]
        parts.append("Recent commits: " + "; ".join(f'"{m}"' for m in msgs))
    if prs:
        pr_titles = [p.get("pr","").replace("None ","")[:70] for p in prs[:3]]
        parts.append("PRs opened: " + "; ".join(pr_titles))
    if syms:
        sym_names = [f"{s.get('qualified_name','')} ({s.get('kind','')})" for s in syms[:4] if s.get('qualified_name')]
        parts.append("Key functions written: " + ", ".join(sym_names))
    return " | ".join(parts) if parts else "No detailed commit/PR data available."


def _departure_risk(sole_files: list, commit_pct: float, risk_score: int) -> str:
    if not sole_files and commit_pct < 5:
        return "Low departure impact — no sole-owned files, minimal commit share."
    sole_paths = [s.get("file_path","") if isinstance(s,dict) else str(s) for s in sole_files[:4]]
    product = any(any(k in p for k in ["src/", "lib/", "packages/", "core/"]) for p in sole_paths)
    infra = any(any(k in p for k in ["workflow", ".github", "docker", "ci", "deploy"]) for p in sole_paths)
    if product and infra:
        return f"HIGH — sole author of both product code and CI/CD ({', '.join(sole_paths[:2])}). Critical departure."
    if product:
        return f"HIGH — sole author of product code ({', '.join(sole_paths[:2])}). Feature delivery at risk."
    if infra:
        return f"MEDIUM — sole CI/CD author ({', '.join(sole_paths[:2])}). Automation breaks, product unaffected."
    return f"MEDIUM — sole author of {len(sole_files)} files, commit share {commit_pct}%."


# ── Multi-contributor ranking ──────────────────────────────────────────

@tool
def rank_all_contributors(project_id: str) -> str:
    """
    Rank ALL contributors across every metric: commits, files owned, symbols
    authored, bus-factor risk, collaboration reach, PR activity, and inferred role.
    Use this for "compare all people", "who is most important", "team overview" questions.
    Args:
        project_id: The project slug
    """
    project_id = _rp(project_id)
    store = _get_engine().store

    all_authors = store.get_commit_authors(project_id)
    total_commits = sum(a.get("commit_count", 0) for a in all_authors) or 1
    bus = store.compute_bus_factor(project_id, min_changes=1)
    sole_by_email = {}
    for b in bus:
        e = b.get("sole_author", "")
        sole_by_email[e] = sole_by_email.get(e, 0) + 1

    all_edges = store.get_edges(project_id, rel_type="COLLABORATES_WITH")
    collab_by_email: dict[str, int] = {}
    for e in all_edges:
        for dev_id in (e["source_id"], e["target_id"]):
            em = dev_id.replace("dev:", "")
            collab_by_email[em] = collab_by_email.get(em, 0) + 1

    # Merge aliases (same person, multiple emails)
    # Group by name
    by_name: dict[str, list] = {}
    for a in all_authors:
        name = a.get("name") or a.get("email", "")
        by_name.setdefault(name, []).append(a)

    rankings = []
    for name, group in by_name.items():
        emails = [g["email"] for g in group]
        commits = sum(g.get("commit_count", 0) for g in group)
        sole = sum(sole_by_email.get(e, 0) for e in emails)
        collab = max(collab_by_email.get(e, 0) for e in emails)
        commit_pct = round(commits / total_commits * 100, 1)

        # File touch counts (SQL-filtered)
        try:
            from chronograph.store.pg_store import PgStore
            touched = len(store.get_file_touch_counts(project_id, emails)) if isinstance(store, PgStore) else 0
            eids = store.get_authored_entity_ids(project_id, emails) if isinstance(store, PgStore) else set()
        except Exception:
            touched = 0
            eids = set()

        # PR stats
        pr_authored = pr_merged = 0
        try:
            from chronograph.store.pg_store import PgStore
            if isinstance(store, PgStore):
                logins = list({e.split("@")[0] for e in emails})
                pr_data = store.get_developer_pr_activity(project_id, logins)
                pr_authored = len(pr_data.get("authored", []))
                pr_merged = len([p for p in pr_data.get("authored", []) if p.get("is_merged")])
        except Exception:
            pass

        # Role inference
        all_files_touched = list(store.get_file_touch_counts(project_id, emails).keys()) if isinstance(store, PgStore) else []
        role = _infer_role(all_files_touched, [])["title"]

        rankings.append({
            "name": name,
            "emails": emails,
            "commits": commits,
            "commit_pct": commit_pct,
            "files_touched": touched,
            "sole_owned_files": sole,
            "symbols_authored": len(eids),
            "collaborator_count": collab,
            "prs_authored": pr_authored,
            "prs_merged": pr_merged,
            "inferred_role": role,
        })

    # Sort by commits desc
    rankings.sort(key=lambda x: x["commits"], reverse=True)

    return json.dumps({
        "project": project_id,
        "total_contributors": len(rankings),
        "ranked_by_commits": rankings,
        "most_commits": rankings[0]["name"] if rankings else None,
        "highest_risk": max(rankings, key=lambda x: x["sole_owned_files"])["name"] if rankings else None,
        "most_collaborative": max(rankings, key=lambda x: x["collaborator_count"])["name"] if rankings else None,
        "most_prs": max(rankings, key=lambda x: x["prs_authored"])["name"] if rankings else None,
    }, indent=2, default=str)


# ── Per-file deep analytics ────────────────────────────────────────────

@tool
def get_file_metadata(project_id: str, file_path: str) -> str:
    """
    Get deep per-file analytics: full author history ranked by contribution,
    first/last commit dates, churn rate, total lines added/deleted,
    complexity stats. Much richer than get_file_content or get_hotspot_files.
    Args:
        project_id: The project slug
        file_path: Exact or partial file path
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    try:
        from chronograph.store.pg_store import PgStore
        if isinstance(store, PgStore):
            meta = store.get_file_metadata(project_id, file_path)
            if not meta:
                return json.dumps({"error": f"No metadata for '{file_path}' in {project_id}. Try re-ingesting."})
            return json.dumps(meta, indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": "file_metadata not available on this store backend"})


@tool
def get_files_by_churn(project_id: str, limit: int = 20) -> str:
    """
    Rank files by total commit count (churn) with per-file author count,
    lines added/deleted, and last touched info.
    Better than get_hotspot_files — includes author count and diff stats.
    Args:
        project_id: The project slug
        limit: Number of files to return (default 20)
    """
    project_id = _rp(project_id)
    store = _get_engine().store
    try:
        from chronograph.store.pg_store import PgStore
        if isinstance(store, PgStore):
            return json.dumps(store.get_files_by_churn(project_id, limit=limit), indent=2, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": "file_metadata not available on this store backend"})


# ── Live file fetch (bypasses ingested data) ──────────────────────────

@tool
def fetch_file_from_github(owner_repo: str, file_path: str, ref: str = "") -> str:
    """
    Fetch the current content of ANY file directly from GitHub API — including
    .md, .yaml, .json and other non-code files not stored in the DB.
    Use this when get_file_content returns 'not found'.
    Args:
        owner_repo: 'owner/repo', e.g. 'toon-format/toon'
        file_path: Path in repo, e.g. 'docs/ecosystem/implementations.md'
        ref: Branch or commit SHA (default: repo default branch)
    """
    from common.config import get_settings
    import base64, requests as _req
    settings = get_settings()
    pat = settings.github_pat
    if not pat:
        return json.dumps({"error": "GITHUB_PAT not set"})

    headers = {
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json",
    }
    url = f"https://api.github.com/repos/{owner_repo}/contents/{file_path}"
    params = {"ref": ref} if ref else {}
    try:
        resp = _req.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code == 404:
            return json.dumps({"error": f"File not found: {file_path} in {owner_repo}"})
        resp.raise_for_status()
        data = resp.json()
        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:
            content = data.get("content", "")
        return json.dumps({
            "file_path": file_path,
            "repo": owner_repo,
            "sha": data.get("sha", ""),
            "size": data.get("size", 0),
            "content": content,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── Semantic Search ────────────────────────────────────────────────────

@tool
def semantic_code_search(project_id: str, query: str, top_k: int = 10) -> str:
    """
    Find code symbols semantically similar to a natural language description.
    Uses NVIDIA NeMo 2048-d embeddings and vector cosine similarity.
    Good for: 'find authentication logic', 'show me error handling code'.
    Args:
        project_id: The project slug
        query: Natural language description of what you're looking for
        top_k: Number of results (default 10)
    """
    project_id = _rp(project_id)
    results = _get_engine().semantic_search(project_id, query, top_k=top_k)
    return json.dumps(results, indent=2)


# ── Tool registry ──────────────────────────────────────────────────────

ALL_TOOLS = [
    # Project overview
    list_ingested_projects,
    get_project_stats,
    # People — always prefer get_developer_profile first
    get_contributors,
    get_developer_profile,
    get_developer_commit_timeline,
    get_developer_expertise_scores,
    get_developer_risk_score,
    compare_developers,
    get_bus_factor,
    get_developer_ownership,
    get_collaboration_pairs,
    # Commits & activity
    get_commit_timeline,
    search_commits,
    get_hotspot_files,
    get_recent_commits,
    # Code intelligence
    search_symbols,
    get_function_source,
    get_file_content,
    get_complexity_report,
    trace_function_calls,
    get_call_graph_edges,
    build_developer_persona,
    rank_all_contributors,
    get_file_metadata,
    get_files_by_churn,
    fetch_file_from_github,
    semantic_code_search,
]
