"""
services/offboarding_service.py
───────────────────────────────
Assembles the offboarding report (Screen 1) from graph analysis. Off the query
hot path — this is an analytical report, not a chat turn.

build_report() composes three parts:
  • header  — SPINE ONLY (name / email / org / reports_to / direct_reports).
              We deliberately DROP the ungated description lists that
              expand_person_profile also returns (decisions_made, etc.), because
              those are not scope-gated; the report's content comes exclusively
              from the scope-gated person_owned_content below.
  • owned   — scope-visible decisions / tasks / facts / events, with receipts.
  • worklist— at-risk topics (sole/near-sole holder) turned into a concrete
              interview question for the manager to run during notice period,
              each with suggested inheritors.

The interview question is a deterministic template in this pass (no LLM). LLM
enrichment of questions and persistence/caching of the package are follow-ups.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.analytics.offboarding import person_owned_content, person_risk_topics
from domain.retrieval.person import expand_person_profile

log = get_logger(__name__)

# Spine-only keys copied into the header. Excludes decisions_made / tasks_assigned
# / facts_stated / events, which expand_person_profile returns UNGATED.
_HEADER_KEYS = ("name", "email", "is_internal", "organization", "reports_to", "direct_reports")


def _interview_question(topic_name: str | None, sole_holder: bool) -> str:
    """Deterministic, honest prompt — frames 'area at risk', never 'what we don't know'."""
    label = topic_name or "this area"
    who = "the only person" if sole_holder else "one of very few people"
    return (
        f"You appear to be {who} with recorded knowledge of \u201c{label}\u201d. "
        f"Before you leave: what should your successor know, who are the key "
        f"contacts, and where does the work and its documentation live?"
    )


@observe(name="offboarding.build-report")
def build_report(
    graph: GraphRepository,
    person_id: str,
    scopes: UserScopes,
    *,
    max_holders: int = 2,
) -> dict:
    """Assemble the offboarding report for ``person_id``, scoped to what the
    requesting manager may see."""
    profile = expand_person_profile(graph, person_id) or {}
    header = {k: profile.get(k) for k in _HEADER_KEYS}
    header["direct_reports"] = header.get("direct_reports") or []

    owned = person_owned_content(graph, person_id, scopes)
    risks = person_risk_topics(graph, person_id, scopes, max_holders=max_holders)

    worklist = [
        {
            "topic_id": r.get("topic_id"),
            "topic_name": r.get("topic_name"),
            "holder_count": r.get("holder_count"),
            "sole_holder": bool(r.get("sole_holder")),
            "my_evidence": r.get("my_evidence"),
            "question": _interview_question(r.get("topic_name"), bool(r.get("sole_holder"))),
            "suggested_inheritors": r.get("other_holders") or [],
        }
        for r in risks
    ]

    log.info("offboarding_report", person_id=person_id,
             owned_decisions=len(owned.get("decisions", [])), at_risk=len(worklist))

    return {
        "person_id": person_id,
        "header": header,
        "owned": owned,
        "worklist": worklist,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
