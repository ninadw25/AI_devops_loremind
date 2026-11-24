"""
api/routes/onboarding.py
────────────────────────
Onboarding guided path (Screen 2).

  POST /api/onboarding/path   → the learning path through a predecessor's world:
                                header + focus areas + key decisions (with why) +
                                people + inherited open work.

Off the hot path. Scopes arrive in the body (gateway Plane B); identity +
per-tenant graph via get_graph. Plain ``def`` handler → FastAPI threadpool.

The interactive person-scoped "ask anything" (POST /api/onboarding/ask) and the
package-as-authorization grant are planned follow-ups; this route serves the
deterministic path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_graph
from api.models import GatewayScopedRequest
from core.logging import get_logger
from core.types import GraphRepository
from services.onboarding_service import build_learning_path

log = get_logger(__name__)
router = APIRouter(tags=["onboarding"])


# ── Request / response models ─────────────────────────────────────────

class OnboardingPathRequest(GatewayScopedRequest):
    person_id: str = Field(..., min_length=1, max_length=128)


class PersonHeaderOut(BaseModel):
    name:           str | None = None
    email:          str | None = None
    is_internal:    bool | None = None
    organization:   str | None = None
    reports_to:     str | None = None
    direct_reports: list[str] = Field(default_factory=list)


class KeyDecisionOut(BaseModel):
    id:          str | None = None
    description: str | None = None
    thread_id:   str | None = None
    rationale:   list[str] = Field(default_factory=list)
    topics:      list[str] = Field(default_factory=list)


class PersonRefOut(BaseModel):
    person_id:   str
    name:        str | None = None
    is_internal: bool = False
    weight:      int = 0


class WorkItemOut(BaseModel):
    id:          str | None = None
    type:        str | None = None
    description: str | None = None
    thread_id:   str | None = None


class LearningPathResponse(BaseModel):
    person_id:      str
    header:         PersonHeaderOut
    focus_areas:    list[str] = Field(default_factory=list)
    key_decisions:  list[KeyDecisionOut] = Field(default_factory=list)
    people:         list[PersonRefOut] = Field(default_factory=list)
    inherited_work: list[WorkItemOut] = Field(default_factory=list)
    generated_at:   str


# ── Endpoint ──────────────────────────────────────────────────────────

@router.post("/onboarding/path", response_model=LearningPathResponse)
def onboarding_path_endpoint(
    req:   OnboardingPathRequest,
    graph: GraphRepository = Depends(get_graph),
):
    return build_learning_path(graph, req.person_id, req.to_user_scopes())
