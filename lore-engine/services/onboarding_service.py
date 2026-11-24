"""
services/onboarding_service.py
──────────────────────────────
Assembles the onboarding guided path (Screen 2): a curriculum through the
predecessor's world, scoped to what the new hire is permitted to see.

build_learning_path() composes:
  • header         — SPINE ONLY (name / org / reports_to / …); ungated content
                     lists from expand_person_profile are excluded, same rule as
                     the offboarding report.
  • focus_areas    — the topics that recur across the predecessor's decisions.
  • key_decisions  — decisions + the WHY (BECAUSE rationale) + topics.
  • people         — who they worked with (comm graph), and roughly how much.
  • inherited_work — open tasks the new hire is picking up (reuses the offboarding
                     owned-content query, tasks bucket).

Deterministic, off the hot path. The interactive person-scoped "ask anything"
and the package-as-authorization-boundary grant are separate follow-ups.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.analytics.offboarding import person_owned_content
from domain.analytics.onboarding import person_key_decisions, person_key_people
from domain.retrieval.person import expand_person_profile

log = get_logger(__name__)

# Spine-only header keys (never the ungated description lists).
_HEADER_KEYS = ("name", "email", "is_internal", "organization", "reports_to", "direct_reports")
_MAX_FOCUS_AREAS = 12


def _blocked(graph: GraphRepository, scopes: UserScopes) -> bool:
    if not graph.available:
        return True
    return scopes.is_empty and not scopes.super_admin


def _focus_areas(key_decisions: list[dict]) -> list[str]:
    """Distinct topics across the predecessor's decisions, order-preserving."""
    seen: dict[str, None] = {}
    for d in key_decisions:
        for t in d.get("topics") or []:
            if t and t not in seen:
                seen[t] = None
    return list(seen)[:_MAX_FOCUS_AREAS]


@observe(name="onboarding.build-learning-path")
def build_learning_path(
    graph: GraphRepository,
    person_id: str,
    scopes: UserScopes,
) -> dict:
    """Assemble the guided path for learning ``person_id``'s role/world."""
    now = datetime.now(timezone.utc).isoformat()
    if _blocked(graph, scopes):
        # Fail-closed: caller can see nothing → an empty path, not a spine leak.
        return {"person_id": person_id, "header": {"direct_reports": []},
                "focus_areas": [], "key_decisions": [], "people": [],
                "inherited_work": [], "generated_at": now}

    profile = expand_person_profile(graph, person_id) or {}
    header = {k: profile.get(k) for k in _HEADER_KEYS}
    header["direct_reports"] = header.get("direct_reports") or []

    key_decisions = person_key_decisions(graph, person_id, scopes)
    people = person_key_people(graph, person_id)
    inherited_work = person_owned_content(graph, person_id, scopes).get("tasks", [])

    log.info("onboarding_path", person_id=person_id,
             decisions=len(key_decisions), people=len(people), tasks=len(inherited_work))

    return {
        "person_id": person_id,
        "header": header,
        "focus_areas": _focus_areas(key_decisions),
        "key_decisions": key_decisions,
        "people": people,
        "inherited_work": inherited_work,
        "generated_at": now,
    }
