"""
api/routes/experts.py
──────────────────────
Expert Search — "who knows about X".

  POST /api/experts   → people ranked by scope-visible evidence on a topic

Deterministic, zero-LLM. Scopes arrive in the body (gateway Plane B); identity
and per-tenant graph routing come via require_gateway (applied at the router
include in api/main.py and inside get_graph). This is the shared primitive the
offboarding report (sole-expert detection) and the continuity radar reuse.

The handler is a plain ``def`` so FastAPI runs the (blocking) Neo4j call in its
threadpool rather than on the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_graph
from api.models import GatewayScopedRequest
from core.logging import get_logger
from core.types import GraphRepository
from domain.retrieval.expertise import find_experts

log = get_logger(__name__)
router = APIRouter(tags=["experts"])


# ── Request / response models ─────────────────────────────────────────

class ExpertsRequest(GatewayScopedRequest):
    topic: str = Field(..., min_length=1, max_length=200)
    k:     int = Field(default=10, ge=1, le=50)


class EvidenceOut(BaseModel):
    id:          str
    type:        str
    description: str | None = None
    thread_id:   str | None = None


class ExpertOut(BaseModel):
    person_id:      str
    name:           str | None = None
    email:          str | None = None
    is_internal:    bool = False
    evidence_count: int = 0
    expert_weight:  int = 0
    total_activity: int = 0
    evidence:       list[EvidenceOut] = Field(default_factory=list)


class ExpertsResponse(BaseModel):
    topic:   str
    experts: list[ExpertOut]


# ── Endpoint ──────────────────────────────────────────────────────────

@router.post("/experts", response_model=ExpertsResponse)
def experts_endpoint(
    req:   ExpertsRequest,
    graph: GraphRepository = Depends(get_graph),
):
    experts = find_experts(graph, req.topic, req.to_user_scopes(), limit=req.k)
    return {"topic": req.topic, "experts": experts}
