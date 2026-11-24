"""
api/routes/people.py
────────────────────
People search — fuzzy name lookup for UI pickers (offboarding / onboarding).

  POST /api/people/search   → matching Person nodes (spine, ungated)

Person nodes are the cross-source spine and carry no scopes (domain/scopes.py),
so this needs no content gating. Behind the gateway (require_gateway) like every
protected route. Plain ``def`` → FastAPI threadpool.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_graph
from core.logging import get_logger
from core.types import GraphRepository
from domain.retrieval.person import search_people

log = get_logger(__name__)
router = APIRouter(tags=["people"])


class PeopleSearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=120)
    k: int = Field(default=10, ge=1, le=25)


class PersonHitOut(BaseModel):
    person_id:     str
    name:          str | None = None
    primary_email: str | None = None
    is_internal:   bool | None = None


class PeopleSearchResponse(BaseModel):
    people: list[PersonHitOut]


@router.post("/people/search", response_model=PeopleSearchResponse)
def people_search_endpoint(
    req:   PeopleSearchRequest,
    graph: GraphRepository = Depends(get_graph),
):
    return {"people": search_people(graph, req.q, limit=req.k)}
