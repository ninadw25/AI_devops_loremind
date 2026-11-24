"""
api/routes/offboarding.py
─────────────────────────
Offboarding cockpit (Screen 1).

  POST /api/offboarding/report   → assembled report for a leaving person:
                                    header + owned content + at-risk worklist.

Off the hot path (analytical). Scopes arrive in the body (gateway Plane B);
identity + per-tenant graph via get_graph. Management-gated at the gateway.
Plain ``def`` handler → FastAPI threadpool for the blocking Neo4j reads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_graph
from api.models import GatewayScopedRequest
from core.logging import get_logger
from core.types import GraphRepository
from services.offboarding_service import build_report

log = get_logger(__name__)
router = APIRouter(tags=["offboarding"])


# ── Request / response models ─────────────────────────────────────────

class OffboardingRequest(GatewayScopedRequest):
    person_id:   str = Field(..., min_length=1, max_length=128)
    max_holders: int = Field(default=2, ge=1, le=10)   # sole/near-sole threshold


class ContentItemOut(BaseModel):
    id:          str | None = None
    type:        str | None = None
    description: str | None = None
    thread_id:   str | None = None


class OwnedContentOut(BaseModel):
    decisions: list[ContentItemOut] = Field(default_factory=list)
    tasks:     list[ContentItemOut] = Field(default_factory=list)
    facts:     list[ContentItemOut] = Field(default_factory=list)
    events:    list[ContentItemOut] = Field(default_factory=list)


class InheritorOut(BaseModel):
    person_id:      str
    name:           str | None = None
    evidence_count: int = 0


class RiskItemOut(BaseModel):
    topic_id:             str | None = None
    topic_name:           str | None = None
    holder_count:         int = 0
    sole_holder:          bool = False
    my_evidence:          int = 0
    question:             str
    suggested_inheritors: list[InheritorOut] = Field(default_factory=list)


class PersonHeaderOut(BaseModel):
    name:           str | None = None
    email:          str | None = None
    is_internal:    bool | None = None
    organization:   str | None = None
    reports_to:     str | None = None
    direct_reports: list[str] = Field(default_factory=list)


class OffboardingReportResponse(BaseModel):
    person_id:    str
    header:       PersonHeaderOut
    owned:        OwnedContentOut
    worklist:     list[RiskItemOut]
    generated_at: str


# ── Endpoint ──────────────────────────────────────────────────────────

@router.post("/offboarding/report", response_model=OffboardingReportResponse)
def offboarding_report_endpoint(
    req:   OffboardingRequest,
    graph: GraphRepository = Depends(get_graph),
):
    return build_report(graph, req.person_id, req.to_user_scopes(), max_holders=req.max_holders)
