"""
Chronograph API router — ingestion + graph building endpoints.
All endpoints under /api/chronograph/* prefix (set in main.py).

Ingestion endpoints:
  POST /ingest-gitlab  — full pipeline: GitLab API → Neo4j/Postgres
  POST /ingest-json    — migrate existing JSON files → Neo4j/Postgres
  POST /build-graph    — build graph relationships from stored data
  GET  /stats          — project stats from store

Tenant isolation: the store is resolved per request from the caller's org
(get_chronograph_store), so ingestion writes and stats reads only ever touch the
caller's own tenant DB. The store is cached/long-lived — handlers must not
disconnect it.
"""

from __future__ import annotations
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_chronograph_store

log = logging.getLogger("api.routes.chronograph")

router = APIRouter(tags=["chronograph"])


# ── Request Models ──

class IngestGitLabRequest(BaseModel):
    project_id: str = Field(..., max_length=200, description="Internal project ID")
    gitlab_project_id: str = Field(..., max_length=50, description="GitLab numeric project ID")
    ref: str = Field(default="main", max_length=100)


class IngestFromJsonRequest(BaseModel):
    project_id: str = Field(..., max_length=200)
    data_dir: str = Field(..., max_length=500, description="Path to JSON data directory")


class BuildGraphRequest(BaseModel):
    project_id: str = Field(..., max_length=200)


# ── Endpoints ──

@router.post("/ingest-gitlab")
async def ingest_gitlab(req: IngestGitLabRequest, store=Depends(get_chronograph_store)):
    """
    Full ingestion: GitLab API → store (commits, code, symbols, embeddings).
    Run once per project. Re-run for updates.
    """
    from core.settings import get_settings
    from chronograph.ingestion.ingest_pipeline import IngestPipeline

    config = get_settings().chronograph_config
    try:
        pipeline = IngestPipeline(store, config)
        stats = await pipeline.ingest_gitlab_project(
            project_id=req.project_id,
            gitlab_project_id=req.gitlab_project_id,
            ref=req.ref,
        )
        return {"ok": True, "stats": stats}
    except Exception as e:
        log.error("Ingestion error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest-json")
async def ingest_json(req: IngestFromJsonRequest, store=Depends(get_chronograph_store)):
    """
    Migrate existing JSON data into store + generate embeddings.
    """
    from core.settings import get_settings
    from chronograph.ingestion.ingest_pipeline import IngestPipeline

    config = get_settings().chronograph_config
    try:
        pipeline = IngestPipeline(store, config)
        stats = await pipeline.ingest_from_json(
            project_id=req.project_id,
            data_dir=req.data_dir,
        )
        return {"ok": True, "stats": stats}
    except Exception as e:
        log.error("JSON ingestion error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/build-graph")
async def build_graph(req: BuildGraphRequest, store=Depends(get_chronograph_store)):
    """
    Build graph relationships (CALLS, AUTHORED_BY, CONTAINS, COLLABORATES_WITH)
    from stored data. Run after ingestion.
    """
    from chronograph.ingestion.graph_builder import GraphBuilder

    try:
        builder = GraphBuilder(store)
        stats = builder.build_all(req.project_id)
        return {"ok": True, "stats": stats}
    except Exception as e:
        log.error("Graph build error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/{project_id:path}")
def get_stats(project_id: str, store=Depends(get_chronograph_store)):
    """Get project stats from store (caller's tenant only)."""
    return store.get_stats(project_id)
