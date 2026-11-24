"""Neo4j projection (build step 7): the pure shaping core.

Pins the two judgment calls in an otherwise mechanical pass-through:

  1. a topic proposed in one claim but properly linked (closed-world) in
     another is 'linked', not 'proposal' — the registry would already treat
     it as real, don't understate that in the graph;
  2. claim_relation rows group by rel_type since Cypher relationship types
     can't be parameterized — each type must get its own edge batch, and an
     unrecognized type must never silently vanish into the wrong bucket.
"""
from builders.project_claims import shape_relations, shape_topics


def test_topic_linked_anywhere_beats_proposal_elsewhere():
    rows = [{"topic": "Payroll Ops", "is_proposal": True},
            {"topic": "Payroll Ops", "is_proposal": False}]
    out = {t["name"]: t["status"] for t in shape_topics(rows)}
    assert out["Payroll Ops"] == "linked"


def test_topic_only_ever_proposed_stays_proposal():
    rows = [{"topic": "New Vendor Program", "is_proposal": True}]
    out = {t["name"]: t["status"] for t in shape_topics(rows)}
    assert out["New Vendor Program"] == "proposal"


def test_topics_are_deduped():
    rows = [{"topic": "Payroll Ops", "is_proposal": False},
            {"topic": "Payroll Ops", "is_proposal": False}]
    assert len(shape_topics(rows)) == 1


def test_relations_group_by_type_and_keep_pairs_intact():
    rows = [{"src_claim_id": "a", "dst_claim_id": "b", "rel_type": "CORROBORATES"},
            {"src_claim_id": "c", "dst_claim_id": "d", "rel_type": "SUPERSEDES"}]
    out = shape_relations(rows)
    assert out == {"CORROBORATES": [{"src": "a", "dst": "b"}],
                   "SUPERSEDES": [{"src": "c", "dst": "d"}]}
