"""
W6: orchestration test for runners/slack_apply_delta.run_apply_delta, using a
fake re-extractor (no LLM, no Neo4j). Verifies: delta → affected sessions →
plan → delta Cypher written.
"""

import json

from common.output_paths import KnowledgePaths
from runners.slack_apply_delta import run_apply_delta

CH = "C1"
TS_ROOT = "1000000.0001"
SID = f"slack:session:{CH}:{TS_ROOT}"


def _ev(ts, body="hi"):
    return {"id": f"{CH}:{ts}", "source": "slack", "source_id": f"{CH}:{ts}",
            "timestamp": "2026-06-01T00:00:00+00:00", "title": body, "body": body,
            "raw": {"ts": str(ts), "channel_id": CH, "slack_meta": {"channel_id": CH},
                    "scope_id": f"slack:channel:{CH}", "source_ref": f"{CH}:{ts}",
                    "source_url": f"https://x.slack.com/archives/{CH}/p{ts}"}}


def _decision(nid):
    return {"subject": {"node_id": nid, "type": "Decision", "description": "switch to Razorpay"},
            "predicate": "SUBJECT_OF",
            "object": {"node_id": "person:1", "type": "Person", "name": "Ravi"},
            "evidence_sentence": "ok lets switch"}


def _setup(tmp_path):
    data_dir = tmp_path
    # current Slack events (sessionize into one session)
    (data_dir / "filtered").mkdir(parents=True)
    (data_dir / "filtered" / "techtest_slack_1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [_ev(TS_ROOT), _ev("1000060.0001")]),
        encoding="utf-8")
    # reconcile delta: the 2nd message was edited
    (data_dir / "slack").mkdir(parents=True)
    (data_dir / "slack" / "techtest_reconcile_delta_2026-06-02.jsonl").write_text(
        json.dumps({"source_id": f"{CH}:1000060.0001", "reconcile_action": "edited",
                    "body": "actually lets stay on Stripe"}) + "\n", encoding="utf-8")
    # prior graph: the session had decision dec:old
    # _prior_triples uses source="slack" so write to the slack-namespaced path
    paths = KnowledgePaths(org="techtest", data_dir=data_dir, source="slack")
    paths.makedirs()
    paths.triples_deduped.write_text(
        json.dumps({SID: {"thread_id": SID, "triples": [_decision("dec:old")]}}),
        encoding="utf-8")
    return data_dir


def test_run_apply_delta_emits_softdelete_and_upsert(tmp_path):
    data_dir = _setup(tmp_path)

    def fake_reextract(affected_threads, org, dd):
        # the edit changed the decision → new node id
        assert set(affected_threads) == {SID}
        return {SID: {"thread_id": SID, "triples": [_decision("dec:new")],
                      "scope_id": f"slack:channel:{CH}", "source_ref": f"{CH}:{TS_ROOT}",
                      "source_url": "https://x/y", "department_tags": ["general"],
                      "sensitivity": "internal"}}

    summary = run_apply_delta("techtest", data_dir, fake_reextract, apply=False)

    assert summary["affected"] == 1
    assert summary["soft_delete"] == 1        # dec:old removed
    assert summary["upsert_sessions"] == 1

    cypher = (data_dir / "cypher" / "techtest_slack_delta.cypher").read_text()
    assert "dec:old" in cypher and "SET n.deleted_at" in cypher   # tombstone
    assert "dec:new" in cypher and "MERGE" in cypher              # re-MERGE


def test_run_apply_delta_no_delta_file_is_noop(tmp_path):
    (tmp_path / "slack").mkdir(parents=True)
    summary = run_apply_delta("techtest", tmp_path, lambda a, o, d: {}, apply=False)
    assert summary["affected"] == 0


def test_jsonable_coerces_str_enum_types_to_plain_strings():
    """Regression: re-extracted nodes carry NodeType str-enums in memory; the
    delta Cypher would emit `:NodeType.DECISION` unless laundered to "Decision".
    (Caught by a live W6 smoke run, 2026-06-06.)"""
    from common.schemas_extraction import NodeType, Predicate
    from runners.slack_apply_delta import _jsonable

    laundered = _jsonable({
        "s": {"triples": [{
            "subject": {"node_id": "dec:1", "type": NodeType.DECISION},
            "predicate": Predicate.SUBJECT_OF,
            "object": {"node_id": "p:1", "type": NodeType.PERSON},
        }]}
    })
    triple = laundered["s"]["triples"][0]
    assert triple["subject"]["type"] == "Decision"     # not "NodeType.DECISION"
    assert triple["object"]["type"] == "Person"
    assert triple["predicate"] == "SUBJECT_OF"
    assert all("NodeType." not in str(v) for v in
               [triple["subject"]["type"], triple["object"]["type"]])
