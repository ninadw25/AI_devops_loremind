"""
api/routes/continuity.py
────────────────────────
Continuity Radar — "which knowledge is stuck in one person's head right now?"

  POST /api/continuity/radar   → headline coverage summary + at-risk topics

Deterministic, zero-LLM, always-on. Scopes arrive in the body (gateway Plane B);
identity + per-tenant graph via get_graph. Management-gated at the gateway
(pulse.view) — lore-engine trusts the gateway for authorization, like every
other protected route here.

Plain ``def`` handler so the (blocking) aggregate Neo4j queries run in FastAPI's
threadpool, not on the event loop.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_graph
from api.models import GatewayScopedRequest
from core.logging import get_logger
from core.types import GraphRepository
from domain.analytics.bus_factor import coverage_summary, topic_coverage

log = get_logger(__name__)
router = APIRouter(tags=["continuity"])


# ── Request / response models ─────────────────────────────────────────

class RadarRequest(GatewayScopedRequest):
    max_holders: int = Field(default=2, ge=1, le=10)   # "at risk" threshold
    k:           int = Field(default=50, ge=1, le=200)  # max at-risk topics returned


class HolderOut(BaseModel):
    person_id:      str
    name:           str | None = None
    is_internal:    bool = False
    evidence_count: int = 0


class TopicRiskOut(BaseModel):
    topic_id:       str
    topic_name:     str | None = None
    holder_count:   int
    sole_holder:    bool
    total_evidence: int = 0
    holders:        list[HolderOut] = Field(default_factory=list)


class CoverageSummaryOut(BaseModel):
    total_topics: int = 0
    sole_held:    int = 0
    at_risk:      int = 0


class RadarResponse(BaseModel):
    summary: CoverageSummaryOut
    topics:  list[TopicRiskOut]


# ── Endpoint ──────────────────────────────────────────────────────────

@router.post("/continuity/radar", response_model=RadarResponse)
def radar_endpoint(
    req:   RadarRequest,
    graph: GraphRepository = Depends(get_graph),
):
    scopes = req.to_user_scopes()
    return {
        "summary": coverage_summary(graph, scopes),
        "topics":  topic_coverage(graph, scopes, max_holders=req.max_holders, limit=req.k),
    }
