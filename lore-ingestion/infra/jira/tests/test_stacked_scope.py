"""Smoke test suite 2/4 — TestStackedScope.

Ingests a Jira issue with fields.security set and verifies:
  - scope_id / extra_scope_ids on the normalized event
  - the builder routes the project scope into the OR-bucket (`scope_ids`) and the
    security level into the AND-bucket (`scope_all_ids`) — see
    docs/RBAC/SCOPE_FILTER_HANDOFF.md
  - an unsecured issue gets no scope_all_ids
  - comments on a secured issue inherit extra_scope_ids from the parent
"""
from __future__ import annotations

import unittest

from infra.jira.normalizer import normalize_issue
from builders.unified_builder import _cypher_for_node, _collect_node_scope_sets

ORG         = "acme"
ENG_KEY     = "ENG"
SECLEVEL_ID = "10001"


def _secured_issue() -> dict:
    return {
        "key": f"{ENG_KEY}-42",
        "fields": {
            "summary": "Deploy new auth service",
            "issuetype": {"name": "Task"},
            "project": {"key": ENG_KEY},
            "security": {"id": SECLEVEL_ID, "name": "Confidential"},
            "reporter": {"accountId": "alice-id"},
            "created": "2024-01-15T10:00:00.000+0000",
        },
    }


def _unsecured_issue() -> dict:
    return {
        "key": f"{ENG_KEY}-7",
        "fields": {
            "summary": "Update public docs",
            "issuetype": {"name": "Task"},
            "project": {"key": ENG_KEY},
            "reporter": {"accountId": "bob-id"},
            "created": "2024-01-15T10:00:00.000+0000",
        },
    }


def _secured_issue_with_comment() -> dict:
    issue = _secured_issue()
    issue["fields"]["comment"] = {
        "comments": [{
            "id": "10100",
            "body": {"type": "text", "text": "Looks good to merge"},
            "author": {"accountId": "alice-id"},
            "created": "2024-01-15T11:00:00.000+0000",
        }]
    }
    return issue


def _node() -> dict:
    return {"type": "Fact", "node_id": "fact:abc123def456", "name": "Auth service shipped"}


def _validated_from(event: dict) -> dict:
    """A one-thread validated dict whose single triple references _node(), so
    _collect_node_scope_sets can attribute the thread's scopes to the node."""
    node = _node()
    return {
        event.get("id", "t1"): {
            "thread_id":       event.get("id", "t1"),
            "scope_id":        event.get("scope_id", ""),
            "extra_scope_ids": event.get("extra_scope_ids", []),
            "source_ref":      event.get("source_ref", ""),
            "source_url":      event.get("source_url", ""),
            "triples": [{"subject": node, "predicate": "REGARDING",
                         "object": {"node_id": "topic:auth", "type": "Topic"}}],
        }
    }


def _scope_sets_for(event: dict):
    """Return (scope_ids, scope_all_ids) the builder would stamp on _node()."""
    or_scopes, and_scopes = _collect_node_scope_sets(_validated_from(event))
    nid = _node()["node_id"]
    return or_scopes.get(nid, set()), and_scopes.get(nid, set())


class TestStackedScope(unittest.TestCase):

    def setUp(self):
        events = normalize_issue(_secured_issue(), ORG)
        self._secured   = events[0]
        self._unsecured = normalize_issue(_unsecured_issue(), ORG)[0]

    # ── 1. scope_id / extra_scope_ids from normalize_issue ───────────

    def test_scope_id(self):
        self.assertEqual(self._secured["scope_id"], f"jira:project:{ENG_KEY}")

    def test_extra_scope_ids(self):
        self.assertEqual(
            self._secured["extra_scope_ids"],
            [f"jira:seclevel:{SECLEVEL_ID}"],
        )

    # ── 2. Scope buckets: project → OR, seclevel → AND ───────────────

    def test_secured_splits_project_or_and_seclevel_and(self):
        scope_ids, scope_all_ids = _scope_sets_for(self._secured)
        self.assertEqual(scope_ids, {f"jira:project:{ENG_KEY}"})
        self.assertEqual(scope_all_ids, {f"jira:seclevel:{SECLEVEL_ID}"})

    def test_node_cypher_writes_both_buckets(self):
        scope_ids, scope_all_ids = _scope_sets_for(self._secured)
        text = "\n".join(_cypher_for_node(_node(), {}, scope_ids, scope_all_ids))
        self.assertIn(f'.scope_ids = ["jira:project:{ENG_KEY}"]', text)
        self.assertIn(f'.scope_all_ids = ["jira:seclevel:{SECLEVEL_ID}"]', text)

    def test_unsecured_has_no_and_bucket(self):
        scope_ids, scope_all_ids = _scope_sets_for(self._unsecured)
        self.assertEqual(scope_ids, {f"jira:project:{ENG_KEY}"})
        self.assertEqual(scope_all_ids, set())
        text = "\n".join(_cypher_for_node(_node(), {}, scope_ids, scope_all_ids))
        self.assertNotIn("scope_all_ids", text)   # AND-bucket omitted when empty

    # ── 3. Comments inherit extra_scope_ids from parent ──────────────

    def test_comment_inherits_extra_scope_ids(self):
        events  = normalize_issue(_secured_issue_with_comment(), ORG)
        comment = next(
            e for e in events if e.get("event_type") == "jira.comment.added"
        )
        self.assertEqual(
            comment["extra_scope_ids"],
            [f"jira:seclevel:{SECLEVEL_ID}"],
        )
