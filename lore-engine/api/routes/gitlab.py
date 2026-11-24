"""
GitLab API router — serves project intelligence from Chronograph (PgStore).
All endpoints under /api/gitlab/* prefix.

Tenant isolation: the chronograph store is resolved PER REQUEST from the caller's
org (get_chronograph_store → registry.chronograph_for(user.org)), so each org only
ever sees code intelligence from its own tenant DB. There is no process-global
store/project — that would serve one tenant's data to everyone.
"""

from __future__ import annotations
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from api.dependencies import get_chronograph_store
from core.settings import get_settings

log = logging.getLogger("api.routes.gitlab")

router = APIRouter(tags=["gitlab"])


# ── Per-request tenant context ───────────────────────────────────────────
# (QueryEngine, project_id) bound to the caller's org. project_id is the first
# repo in THAT org's store — every org has its own DB, so this is their project,
# never a cross-tenant one.
# ponytail: single-project-per-org; thread an explicit project_id param through
# when an org can hold multiple repos.

def get_gitlab_ctx(store=Depends(get_chronograph_store)):
    from chronograph.query.query_engine import QueryEngine

    with store._session() as s:
        row = s.execute(
            text("SELECT project_id FROM chronograph.repos LIMIT 1")
        ).first()
    if not row:
        raise HTTPException(
            status_code=503,
            detail="GitLab data not loaded for this organization. Run ingestion first.",
        )
    return QueryEngine(store, get_settings().chronograph_config), row[0]


# ── Request Models ──

class ChatRequest(BaseModel):
    question: str = Field(..., max_length=500)


# ── Endpoints ──

@router.get("/stats")
def get_stats(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_stats(pid)


@router.get("/project")
def get_project(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    project = engine.store.get_project(pid)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/people")
def get_people(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_people(pid)


@router.get("/members")
def get_members(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.store.get_members(pid)


@router.get("/repos")
def get_repos(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    project = engine.store.get_project(pid)
    authors = engine.get_people(pid)
    return [{
        **(project or {}),
        "total_commits": sum(a.get("commit_count", 0) for a in authors),
        "contributors": len(authors),
    }]


@router.get("/person/{name}")
def get_person(name: str, ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    authors = engine.get_people(pid)
    match = next((a for a in authors if name.lower() in a.get("name", "").lower()
                   or name.lower() in a.get("email", "").lower()), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Person '{name}' not found")
    return match


@router.get("/hotspots")
def get_hotspots(limit: int = Query(default=20, ge=1, le=100), ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_hotspots(pid, limit=limit)


@router.get("/bus-factor")
def get_bus_factor(min_changes: int = Query(default=5, ge=1), ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_bus_factor(pid, min_changes=min_changes)


@router.get("/temporal-coupling")
def get_temporal_coupling(min_co_changes: int = Query(default=5, ge=2), ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    all_cf = engine.store.get_all_commit_files(pid)
    from chronograph.diffing.change_classifier import compute_temporal_coupling
    file_changes = {sha: [f["path"] for f in files] for sha, files in all_cf.items()}
    couplings = compute_temporal_coupling(file_changes)
    return {"couplings": couplings, "total": len(couplings)}


@router.get("/expertise")
def get_expertise(ctx=Depends(get_gitlab_ctx)):
    """Per-author file expertise computed from commit_files + commits."""
    engine, pid = ctx
    return engine.store.get_developer_file_expertise(pid, limit=200)


@router.get("/timeline")
def get_timeline(bucket: str = Query(default="month", pattern="^(day|week|month)$"), ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_timeline(pid, bucket=bucket)


@router.get("/search")
def search(q: str = Query(default="", max_length=500), ctx=Depends(get_gitlab_ctx)):
    if not q:
        return {"results": []}
    engine, pid = ctx
    return {"results": engine.search_commits(pid, q)}


@router.post("/chat")
async def chat(req: ChatRequest, ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.chat(pid, req.question)


@router.get("/graph")
def get_graph(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    nodes = []
    edges = []
    project = engine.store.get_project(pid)
    if project:
        nodes.append({"id": f"repo:{pid}", "type": "repository", "label": project.get("full_name", pid)})
    for a in engine.get_people(pid):
        aid = f"person:{a['email']}"
        nodes.append({"id": aid, "type": "person", "label": a["name"], "data": {"commits": a["commit_count"]}})
        edges.append({"source": aid, "target": f"repo:{pid}", "type": "CONTRIBUTES_TO", "weight": a["commit_count"]})
    return {"nodes": nodes, "edges": edges}


# ── Code-Level Endpoints ──

@router.get("/code/stats")
def code_stats(ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    symbols = engine.store.get_symbol_count(pid)
    files = engine.store.get_source_file_count(pid)
    embeds = engine.store.get_embedding_count(pid)
    complex_top = engine.get_complexity(pid, limit=1)
    return {
        "source_files": files,
        "symbols": symbols,
        "embeddings": embeds,
        "max_complexity": complex_top[0]["complexity"]["cyclomatic"] if complex_top else 0,
    }


@router.get("/code/file/{file_path:path}")
def code_file(file_path: str, ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    result = engine.get_file_content(pid, file_path)
    if not result:
        raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")
    return result


@router.get("/code/search")
def code_search(
    q: str = Query(..., max_length=200),
    kind: Optional[str] = Query(default=None, pattern="^(function|method|class)$"),
    limit: int = Query(default=20, ge=1, le=100),
    ctx=Depends(get_gitlab_ctx),
):
    engine, pid = ctx
    return engine.search_symbols(pid, q, kind=kind, limit=limit)


@router.get("/code/semantic")
async def code_semantic(
    q: str = Query(..., max_length=500),
    top_k: int = Query(default=10, ge=1, le=50),
    ctx=Depends(get_gitlab_ctx),
):
    """Semantic code search — finds symbols by meaning, not just name."""
    engine, pid = ctx
    return engine.semantic_search(pid, q, top_k=top_k)


@router.get("/code/function/{name:path}")
def code_function(name: str, ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    result = engine.get_function_code(pid, name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Function '{name}' not found")
    return result


@router.get("/code/complexity")
def code_complexity(limit: int = Query(default=20, ge=1, le=100), ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.get_complexity(pid, limit=limit)


@router.get("/code/trace/{function_name:path}")
def code_trace(function_name: str, ctx=Depends(get_gitlab_ctx)):
    engine, pid = ctx
    return engine.trace_function(pid, function_name)
