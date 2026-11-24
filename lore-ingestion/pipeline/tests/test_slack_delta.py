"""
W6: tests for the graph-side reconcile-delta application (builders/slack_delta.py).
Pure planning + Cypher emit — no LLM, no Neo4j.
"""

from builders.slack_delta import (
    DeltaPlan,
    affected_sessions_from_delta,
    emit_delta_cypher,
    emit_soft_delete_cypher,
    plan_delta,
    session_node_ids,
)

SID = "slack:session:C1:111.2"


def _node(nid, ntype, desc="x"):
    return {"node_id": nid, "type": ntype, "description": desc}


def _thread(sid, triples, **extra):
    base = {"thread_id": sid, "triples": triples, "scope_id": "slack:channel:C1",
            "source_ref": "C1:111.2", "source_url": "https://x/y",
            "department_tags": ["general"], "sensitivity": "internal"}
    base.update(extra)
    return base


def _decision_triple(dec_id, person="Ravi"):
    return {"subject": _node(dec_id, "Decision", "switch to Razorpay"),
            "predicate": "SUBJECT_OF",
            "object": {"node_id": "person:1", "type": "Person", "name": person},
            "evidence_sentence": "ok lets switch"}


def test_session_node_ids_collects_knowledge_only():
    th = _thread(SID, [_decision_triple("dec:a")])
    ids = session_node_ids(th)
    assert ids == {"dec:a": "Decision"}        # Person excluded (spine, not gated)


def test_affected_sessions_maps_source_id():
    sessions = {SID: {"emails": [{"source_id": "C1:111.2"}, {"source_id": "C1:131.2"}]}}
    delta = [{"source_id": "C1:131.2", "reconcile_action": "edited"}]
    assert affected_sessions_from_delta(delta, sessions) == {SID}
    # unknown source_id → no session
    assert affected_sessions_from_delta([{"source_id": "CZ:9"}], sessions) == set()


def test_plan_delta_edit_replaces_node():
    prior = {SID: _thread(SID, [_decision_triple("dec:old")])}
    new = {SID: _thread(SID, [_decision_triple("dec:new")])}
    plan = plan_delta(prior, new, {SID})
    assert plan.soft_delete == [("Decision", "dec:old")]
    assert plan.upsert_session_ids == [SID]


def test_plan_delta_whole_session_deleted():
    prior = {SID: _thread(SID, [_decision_triple("dec:a"), _decision_triple("dec:b")])}
    new = {}  # session vanished entirely
    plan = plan_delta(prior, new, {SID})
    assert sorted(plan.soft_delete) == [("Decision", "dec:a"), ("Decision", "dec:b")]
    assert plan.upsert_session_ids == []


def test_plan_delta_unchanged_node_not_touched():
    prior = {SID: _thread(SID, [_decision_triple("dec:keep")])}
    new = {SID: _thread(SID, [_decision_triple("dec:keep")])}
    plan = plan_delta(prior, new, {SID})
    assert plan.soft_delete == []               # nothing removed
    assert plan.upsert_session_ids == [SID]     # still re-MERGEd (idempotent)


def test_emit_soft_delete_cypher_shape():
    c = emit_soft_delete_cypher("Decision", "dec:old")
    assert "MATCH (n:Decision {decision_id: 'dec:old'})" in c
    assert "SET n.deleted_at = datetime()" in c
    assert "n.scope_ids = []" in c          # fail-closed: empty OR-bucket = invisible
    assert "n.scope_all_ids = []" in c


def test_emit_delta_cypher_combines_softdelete_and_upsert():
    prior = {SID: _thread(SID, [_decision_triple("dec:old")])}
    new = {SID: _thread(SID, [_decision_triple("dec:new")])}
    plan = plan_delta(prior, new, {SID})
    cypher = emit_delta_cypher(plan, new)
    assert "SET n.deleted_at" in cypher and "dec:old" in cypher        # soft-delete
    assert "MERGE" in cypher and "dec:new" in cypher                   # upsert
    # deterministic / idempotent emission
    assert cypher == emit_delta_cypher(plan_delta(prior, new, {SID}), new)
