"""Smoke test suite 4/4 — TestAclGate.

Pure-Python simulation of the query-time Cypher ACL filter:

    WHERE k.scope_id IN $allowed
      AND all(s IN k.extra_scope_ids WHERE s IN $allowed)

No Neo4j connection required. The helper _acl_allows() mirrors the
predicate exactly so tests document the intended enforcement contract.
"""
from __future__ import annotations

import unittest

from infra.jira.normalizer import normalize_issue

ORG           = "acme"
ENG_KEY       = "ENG"
OPS_KEY       = "OPS"
SECLEVEL_A    = "10001"
SECLEVEL_B    = "10002"

SCOPE_ENG     = f"jira:project:{ENG_KEY}"
SCOPE_OPS     = f"jira:project:{OPS_KEY}"
SCOPE_SEC_A   = f"jira:seclevel:{SECLEVEL_A}"
SCOPE_SEC_B   = f"jira:seclevel:{SECLEVEL_B}"


def _acl_allows(scope_id: str | None, extra_scope_ids: list[str], allowed: set[str]) -> bool:
    """Python equivalent of the Cypher WHERE clause."""
    if not scope_id or scope_id not in allowed:
        return False
    return all(s in allowed for s in extra_scope_ids)


def _secured_issue(key: str = f"{ENG_KEY}-42", sec_ids: list[str] | None = None) -> dict:
    security = {"id": sec_ids[0], "name": "Confidential"} if sec_ids else None
    issue = {
        "key": key,
        "fields": {
            "summary": "Confidential deployment",
            "issuetype": {"name": "Task"},
            "project": {"key": key.split("-")[0]},
            "reporter": {"accountId": "alice-id"},
            "created": "2024-01-15T10:00:00.000+0000",
        },
    }
    if security:
        issue["fields"]["security"] = security
    return issue


class TestAclGate(unittest.TestCase):

    # ── 1. Project scope alone does not clear a security level ───────

    def test_project_scope_only_blocked_by_seclevel(self):
        allowed = {SCOPE_ENG}   # no seclevel
        self.assertFalse(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A], allowed))

    # ── 2. Full grant (project + seclevel) allows access ─────────────

    def test_full_grant_allows_access(self):
        allowed = {SCOPE_ENG, SCOPE_SEC_A}
        self.assertTrue(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A], allowed))

    # ── 3. Wrong project blocks regardless of seclevel ───────────────

    def test_wrong_project_blocked(self):
        allowed = {SCOPE_OPS, SCOPE_SEC_A}   # user is in OPS, not ENG
        self.assertFalse(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A], allowed))

    # ── 4. Unsecured issue needs only project scope ───────────────────

    def test_unsecured_issue_allowed_with_project_scope(self):
        allowed = {SCOPE_ENG}
        self.assertTrue(_acl_allows(SCOPE_ENG, [], allowed))

    # ── 5. All stacked security levels required ───────────────────────

    def test_all_seclevels_required(self):
        allowed = {SCOPE_ENG, SCOPE_SEC_A, SCOPE_SEC_B}
        self.assertTrue(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A, SCOPE_SEC_B], allowed))

    def test_missing_one_of_two_seclevels_blocked(self):
        allowed = {SCOPE_ENG, SCOPE_SEC_A}   # missing SCOPE_SEC_B
        self.assertFalse(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A, SCOPE_SEC_B], allowed))

    # ── 6. Empty allowed set blocks everything ────────────────────────

    def test_empty_allowed_blocks_all(self):
        self.assertFalse(_acl_allows(SCOPE_ENG, [], set()))
        self.assertFalse(_acl_allows(SCOPE_ENG, [SCOPE_SEC_A], set()))

    # ── 7. Round-trip through normalize_issue ────────────────────────

    def test_round_trip_normalize_then_acl(self):
        secured   = normalize_issue(_secured_issue(sec_ids=[SECLEVEL_A]), ORG)[0]
        unsecured = normalize_issue(_secured_issue(key=f"{ENG_KEY}-7"), ORG)[0]

        full_access    = {SCOPE_ENG, SCOPE_SEC_A}
        partial_access = {SCOPE_ENG}            # project only, no seclevel

        # user with full access sees both
        self.assertTrue(_acl_allows(
            secured["scope_id"], secured["extra_scope_ids"], full_access))
        self.assertTrue(_acl_allows(
            unsecured["scope_id"], unsecured["extra_scope_ids"], full_access))

        # user with partial access sees only the unsecured issue
        self.assertFalse(_acl_allows(
            secured["scope_id"], secured["extra_scope_ids"], partial_access))
        self.assertTrue(_acl_allows(
            unsecured["scope_id"], unsecured["extra_scope_ids"], partial_access))
