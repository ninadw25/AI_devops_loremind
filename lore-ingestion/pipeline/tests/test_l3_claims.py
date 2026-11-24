"""L3 claim layer (build step 7): the pure resolution / grounding / scope core.

Pins the properties that make a claim trustworthy and ACL-correct:

  1. claim ids are deterministic — a re-run of the same statement is idempotent,
     not a duplicate;
  2. entity names resolve to registry persons by exact (normalized) match, and
     unknown names degrade gracefully (kept as text, person_id null) — never a
     fuzzy merge (that's an ACL risk, step 9);
  3. evidence grounds to the EXACT source message it came from, and cross-message
     stitches / reflowed tables are flagged (grounded=False) not dropped;
  4. scope_ids inherit as the union of source events, fail-closed to invisible.
"""
from builders.l3_claims import (EntityRef, LLMClaim, ThreadExtraction, build_rows,
                                claim_id, ground_evidence, inherit_scopes,
                                resolve_entity)

NAME_TO_PERSON = {"mukesh sethia": "person:mukesh@ezobooks.in",
                  "gaurav kumar": "person:gk@ezobooks.in"}


def _msg(event_id, body, title="", scopes=None):
    return {"event_id": event_id, "title": title, "body": body,
            "actors": [], "primary_actor": "", "scope_ids": scopes or []}


def test_claim_id_is_deterministic_and_kind_sensitive():
    a = claim_id("ez", "Decision", "Mukesh revised the salary on 2025-11-01.")
    b = claim_id("ez", "Decision", "Mukesh revised the salary on 2025-11-01.")
    c = claim_id("ez", "Decision", "Mukesh  revised  the salary on 2025-11-01.")  # spacing
    d = claim_id("ez", "Fact", "Mukesh revised the salary on 2025-11-01.")        # kind
    assert a == b == c, "same statement (mod whitespace) → same id"
    assert a != d, "different kind → different id"


def test_entity_resolution_exact_only():
    assert resolve_entity("Mukesh Sethia", NAME_TO_PERSON) == ("person:mukesh@ezobooks.in", "person")
    assert resolve_entity("mukesh  sethia", NAME_TO_PERSON)[0] == "person:mukesh@ezobooks.in"
    # an org / external name resolves to nothing — kept as text, never guessed
    assert resolve_entity("Knowlarity Communications", NAME_TO_PERSON) == (None, "unresolved")
    # near-miss must NOT fuzzy-merge (ACL safety)
    assert resolve_entity("Mukesh", NAME_TO_PERSON) == (None, "unresolved")


def test_evidence_grounds_to_the_exact_message():
    msgs = [_msg("e1", "Please carry your documents on joining day."),
            _msg("e2", "Your revised salary will be Rs. 20330 per month.")]
    ev_id, grounded = ground_evidence("Your revised salary will be Rs. 20330 per month.", msgs)
    assert grounded and ev_id == "e2", "quote traces to its source message"


def test_cross_message_stitch_is_flagged_not_lost():
    msgs = [_msg("e1", "Rs. 12083 has been credited via NEFT"),
            _msg("e2", "transaction reference UTR99887766 dated 20-MAR")]
    # a quote stitched from both messages matches neither alone
    ev_id, grounded = ground_evidence("credited via NEFT transaction reference UTR99887766", msgs)
    assert grounded is False and ev_id is None, "stitch flagged, so citation stays honest"


def test_scopes_inherit_as_union_and_fail_closed():
    msgs = [_msg("e1", "x", scopes=["person:a@x.com"]),
            _msg("e2", "y", scopes=["person:b@x.com", "person:a@x.com"])]
    scope, scope_all = inherit_scopes(msgs)
    assert scope == ["person:a@x.com", "person:b@x.com"] and scope_all == []
    # no source scope → empty → invisible, never public
    assert inherit_scopes([_msg("e3", "z")]) == ([], [])


def test_build_rows_end_to_end():
    conv = {"conversation_id": "c1", "emails": [
        _msg("e1", "Your revised salary will be Rs. 20330 per month.",
             scopes=["person:mukesh@ezobooks.in"])]}
    ext = ThreadExtraction(digest="Mukesh revised a salary.", claims=[LLMClaim(
        kind="Decision",
        statement="On 2025-11-01 Mukesh Sethia revised the salary to Rs. 20330/month.",
        entities=[EntityRef(name="Mukesh Sethia", role="decider"),
                  EntityRef(name="Knowlarity Communications", role="counterparty")],
        topics=["Employee Compensation", "NEW: Payroll Ops"],
        evidence_quote="Your revised salary will be Rs. 20330 per month.")])
    rows = build_rows(ext, conv, "ez", NAME_TO_PERSON)

    assert len(rows["claims"]) == 1
    c = rows["claims"][0]
    assert c["evidence_grounded"] and c["evidence_event_id"] == "e1"
    assert c["scope_ids"] == ["person:mukesh@ezobooks.in"]
    # one entity resolved to a person, one kept as unresolved text
    kinds = {e["entity_ref"]: e["person_id"] for e in rows["entities"]}
    assert kinds["Mukesh Sethia"] == "person:mukesh@ezobooks.in"
    assert kinds["Knowlarity Communications"] is None
    # topic linked vs proposed
    props = {t["topic"]: t["is_proposal"] for t in rows["topics"]}
    assert props["Employee Compensation"] is False and props["Payroll Ops"] is True
    assert rows["digest"]["claim_count"] == 1
