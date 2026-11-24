"""
Regression tests for Slack scope_id plumbing through builders/unified_builder.py.

Covers the removal of the raw-file `slack_scope_by_thread` backfill (LOR-39 cleanup):
scope_id is carried end-to-end by the pipeline, so the builder must NOT reconstruct
it from raw .jsonl files. When an ACL-bearing thread (source_ref set) is missing
scope info, the builder must REFUSE to build (UnscopedThreads) unless
--allow-unscoped is passed, in which case it warns and fails closed (no IN_SCOPE
edge) rather than silently patching it.
"""

import json

import pytest

import builders.unified_builder as ub
import pipeline.deduplicator as dedup
from common.stage_gate import UnscopedThreads
import common.stage_gate as stage_gate


def _write_inputs(tmp_path, org, validated):
    paths_root = tmp_path / "knowledge"
    (paths_root / "triples").mkdir(parents=True)
    (paths_root / "registry").mkdir(parents=True)
    (paths_root / "triples" / f"{org}_triples_validated.json").write_text(
        json.dumps(validated), encoding="utf-8"
    )
    (paths_root / "registry" / f"{org}_person_registry.json").write_text(
        json.dumps({"email_to_person_id": {}, "persons": []}), encoding="utf-8"
    )


def _validated_fixture():
    return {
        "thread_with_scope": {
            "thread_id": "thread_with_scope",
            "triples": [],
            "scope_id": "slack:channel:C123",
            "source_ref": "C123:1700000000.000100",
            "source_url": "https://x.slack.com/archives/C123/p1700000000000100",
        },
        # Slack thread (source_ref set) that lost scope_id somewhere upstream.
        "thread_missing_scope": {
            "thread_id": "thread_missing_scope",
            "triples": [],
            "source_ref": "C999:1700000000.000200",
            "source_url": "https://x.slack.com/archives/C999/p1700000000000200",
        },
        # Gmail thread: no source_ref, legitimately no scope_id — must not be flagged.
        "gmail_thread": {
            "thread_id": "gmail_thread",
            "triples": [],
        },
    }


def test_load_inputs_no_backfill_key_and_preserves_scope(tmp_path):
    org = "testorg"
    _write_inputs(tmp_path, org, _validated_fixture())

    # Fixture contains an unscoped Slack thread; allow_unscoped exercises the
    # warn-and-continue path so we can inspect the loaded payload.
    result = ub.load_inputs(tmp_path, org, allow_unscoped=True)

    # Backfill machinery is gone from the returned payload.
    assert "slack_scope_by_thread" not in result

    validated = result["validated"]
    # Present scope_id is preserved untouched.
    assert validated["thread_with_scope"]["scope_id"] == "slack:channel:C123"
    # Missing scope_id is NOT reconstructed — fail closed.
    assert "scope_id" not in validated["thread_missing_scope"] \
        or not validated["thread_missing_scope"].get("scope_id")


def test_load_inputs_raises_on_acl_thread_missing_scope(tmp_path):
    """Default behavior: an ACL-bearing thread without scope info aborts the build."""
    org = "testorg"
    _write_inputs(tmp_path, org, _validated_fixture())

    with pytest.raises(UnscopedThreads) as exc:
        ub.load_inputs(tmp_path, org)

    # The offending thread is named; the Gmail thread is not flagged.
    assert "thread_missing_scope" in str(exc.value)
    assert "gmail_thread" not in str(exc.value)
    assert "1/3" in str(exc.value)


def test_load_inputs_allow_unscoped_downgrades_to_warning(tmp_path, monkeypatch):
    org = "testorg"
    _write_inputs(tmp_path, org, _validated_fixture())

    warnings = []
    monkeypatch.setattr(stage_gate._log, "warning",
                        lambda msg, *a, **k: warnings.append((msg % a) if a else msg))

    ub.load_inputs(tmp_path, org, allow_unscoped=True)

    scope_warnings = [w for w in warnings if "ACL-bearing" in w]
    assert len(scope_warnings) == 1, warnings
    assert "thread_missing_scope" in scope_warnings[0]


def test_load_inputs_scoped_via_scope_ids_list_passes(tmp_path):
    """A thread carrying scope via the scope_ids list (Gmail-style) instead of
    scope_id must not trip the gate even when source_ref is set."""
    org = "testorg"
    validated = {
        "outlook_style_thread": {
            "thread_id": "outlook_style_thread",
            "triples": [],
            "source_ref": "msg:abc123",
            "scope_ids": ["person:p1", "person:p2"],
        },
    }
    _write_inputs(tmp_path, org, validated)

    result = ub.load_inputs(tmp_path, org)  # must not raise
    assert result["validated"]["outlook_style_thread"]["scope_ids"] == [
        "person:p1", "person:p2"]


def test_load_inputs_warns_on_unscoped_gmail_legacy_thread(tmp_path, monkeypatch):
    """Gmail/legacy threads (no source_ref) without scope warn but never raise."""
    org = "testorg"
    validated = {
        "gmail_thread": {"thread_id": "gmail_thread", "triples": []},
    }
    _write_inputs(tmp_path, org, validated)

    warnings = []
    monkeypatch.setattr(ub.log, "warning",
                        lambda msg, *a, **k: warnings.append((msg % a) if a else msg))

    ub.load_inputs(tmp_path, org)  # must not raise

    legacy_warnings = [w for w in warnings if "Gmail/legacy" in w]
    assert len(legacy_warnings) == 1, warnings
    assert "gmail_thread" in legacy_warnings[0]


def test_load_inputs_prefers_deduped_file_and_keeps_scope(tmp_path):
    """The builder reads triples_deduped when present; scope_id must survive there."""
    org = "testorg"
    _write_inputs(tmp_path, org, _validated_fixture())
    # Write a deduped file with a DISTINCT scope_id so we can prove it's the one read.
    deduped = {
        "thread_with_scope": {
            "thread_id": "thread_with_scope",
            "triples": [],
            "scope_id": "slack:channel:DEDUPED",
            "source_ref": "Cdd:1700000000.000900",
            "source_url": "https://x.slack.com/archives/Cdd/p1700000000000900",
        }
    }
    (tmp_path / "knowledge" / "triples" / f"{org}_triples_deduped.json").write_text(
        json.dumps(deduped), encoding="utf-8"
    )

    result = ub.load_inputs(tmp_path, org)
    # Read from the deduped file (preference), with scope_id intact.
    assert result["validated"]["thread_with_scope"]["scope_id"] == "slack:channel:DEDUPED"


def test_deduplicator_preserves_thread_scope_metadata():
    """apply_mapping mutates only triples; thread-level scope_id/source_ref survive,
    even when nodes are merged and duplicate triples are dropped."""
    validated = {
        "t1": {
            "thread_id": "t1",
            "scope_id": "slack:channel:C123",
            "source_ref": "C123:1700000000.000100",
            "source_url": "https://x.slack.com/archives/C123/p1700000000000100",
            "triples": [
                {"subject": {"node_id": "dec:a", "type": "Decision"},
                 "predicate": "REGARDING",
                 "object": {"node_id": "topic:x", "type": "Topic"}},
                {"subject": {"node_id": "dec:b", "type": "Decision"},
                 "predicate": "REGARDING",
                 "object": {"node_id": "topic:x", "type": "Topic"}},
            ],
        }
    }
    # Merge dec:b → dec:a, which also collapses the two now-identical triples.
    dedup.apply_mapping(validated, {"dec:b": "dec:a"})

    thread = validated["t1"]
    assert thread["scope_id"] == "slack:channel:C123"
    assert thread["source_ref"] == "C123:1700000000.000100"
    assert thread["source_url"].startswith("https://x.slack.com/")
    # The merge actually happened (duplicate triple dropped) — proving we exercised it.
    assert len(thread["triples"]) == 1


def test_node_scope_ids_union_across_sessions():
    """scope_ids is the UNION of every session a node appears in (OR-bucket).
    A node in #sales and #devops → both channels → either member can see it."""
    validated = {
        "sess_sales": {
            "thread_id": "sess_sales", "scope_id": "slack:channel:sales",
            "triples": [{"subject": {"node_id": "dec:x", "type": "Decision"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:p", "type": "Topic"}}],
        },
        "sess_devops": {
            "thread_id": "sess_devops", "scope_id": "slack:channel:devops",
            "triples": [{"subject": {"node_id": "dec:x", "type": "Decision"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:p", "type": "Topic"}}],
        },
    }
    or_scopes, and_scopes = ub._collect_node_scope_sets(validated)
    assert or_scopes["dec:x"] == {"slack:channel:sales", "slack:channel:devops"}
    assert and_scopes["dec:x"] == set()

    lines = ub._cypher_for_node({"type": "Decision", "node_id": "dec:x"},
                                validated["sess_sales"],
                                or_scopes["dec:x"], and_scopes["dec:x"])
    joined = "\n".join(lines)
    # sorted union written as the scope_ids array; no AclScope / IN_SCOPE anymore
    assert '.scope_ids = ["slack:channel:devops", "slack:channel:sales"]' in joined
    assert "IN_SCOPE" not in joined and "AclScope" not in joined


def test_node_fail_closed_without_scope_id():
    """A Slack node whose session lost scope_id gets an empty scope_ids → invisible."""
    validated = {
        "sess": {
            "thread_id": "sess", "source_ref": "C999:1.2",  # slack, but no scope_id
            "triples": [{"subject": {"node_id": "dec:y", "type": "Decision"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:q", "type": "Topic"}}],
        },
    }
    or_scopes, _and = ub._collect_node_scope_sets(validated)
    assert or_scopes.get("dec:y", set()) == set()
    lines = ub._cypher_for_node({"type": "Decision", "node_id": "dec:y"},
                                validated["sess"], set(), set())
    assert ".scope_ids = []" in "\n".join(lines)


def test_node_jira_stacked_scope_goes_to_and_bucket():
    """Jira project = OR-bucket; security level = AND-bucket (scope_all_ids)."""
    validated = {
        "issue": {
            "thread_id": "issue", "scope_id": "jira:project:ENG",
            "extra_scope_ids": ["jira:seclevel:10000"],
            "triples": [{"subject": {"node_id": "fact:z", "type": "Fact"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:r", "type": "Topic"}}],
        },
    }
    or_scopes, and_scopes = ub._collect_node_scope_sets(validated)
    assert or_scopes["fact:z"] == {"jira:project:ENG"}
    assert and_scopes["fact:z"] == {"jira:seclevel:10000"}
    joined = "\n".join(ub._cypher_for_node({"type": "Fact", "node_id": "fact:z"},
                                           validated["issue"],
                                           or_scopes["fact:z"], and_scopes["fact:z"]))
    assert '.scope_ids = ["jira:project:ENG"]' in joined
    assert '.scope_all_ids = ["jira:seclevel:10000"]' in joined
