"""Regression test for the silent-ACL-hole fix.

A project whose permission scheme cannot be read (e.g. a team-managed project
that returns 403) must NOT be swallowed silently. Discovery must:
  1. emit a WARNING naming the project,
  2. record it in artifact["acl_incomplete_projects"] with the HTTP status,
  3. still create the project scope but with ZERO browse grants (fail-closed).

The "good" project in the same run must be unaffected — proving the warning
path is scoped to the failing project, not a blanket bail-out.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import requests

from infra.jira.discovery import run_discovery

ORG        = "acme"
GOOD_KEY   = "GOOD"
SCRUM_KEY  = "SCRUM"   # team-managed; permission scheme 403s
USER_ALICE = "712020:alice-uuid"


async def _fake_get(endpoint, params=None):
    if "/users/search" in endpoint:
        return [
            {"accountId": USER_ALICE, "emailAddress": "alice@acme.io",
             "displayName": "Alice", "accountType": "atlassian"},
        ]
    # No groups in this fixture — keep the focus on the project ACL path.
    if "/group/bulk" in endpoint or "/groups/picker" in endpoint or "/groups" in endpoint:
        return {"values": []}
    return {}


def _permission_scheme(project_key):
    if project_key == SCRUM_KEY:
        # Mirror what JiraFetcher raises on a real 403 (non-skippable -> re-raised).
        raise requests.HTTPError(
            "403 Client Error: Forbidden",
            response=SimpleNamespace(status_code=403),
        )
    return {"id": "100"}


def _scheme_permissions(scheme_id):
    return [
        {"permission": "BROWSE_PROJECTS",
         "holder": {"type": "user", "parameter": USER_ALICE}},
    ]


def _make_fetcher() -> AsyncMock:
    f = AsyncMock()
    f.get.side_effect = _fake_get
    f.list_projects.return_value = [
        {"key": GOOD_KEY,  "name": "Good Project"},
        {"key": SCRUM_KEY, "name": "Scrum (team-managed)"},
    ]
    f.fetch_project_permission_scheme.side_effect    = _permission_scheme
    f.fetch_permission_scheme_permissions.side_effect = _scheme_permissions
    f.fetch_security_schemes.return_value = []
    return f


class TestAclIncomplete(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        with self.assertLogs("infra.jira.discovery", level="WARNING") as cm:
            self._artifact = await run_discovery(
                ORG, _make_fetcher(), output_dir=Path(self._tmp.name)
            )
        self._logs = cm.output

    def tearDown(self):
        self._tmp.cleanup()

    # ── 1. The failure is recorded, not swallowed ─────────────────────

    def test_incomplete_list_populated(self):
        incomplete = self._artifact.get("acl_incomplete_projects", [])
        keys = {p["project_key"] for p in incomplete}
        self.assertIn(SCRUM_KEY, keys)

    def test_incomplete_records_http_status(self):
        scrum = next(p for p in self._artifact["acl_incomplete_projects"]
                     if p["project_key"] == SCRUM_KEY)
        self.assertEqual(scrum["http_status"], 403)
        self.assertIn("403", scrum["reason"])

    # ── 2. A warning was actually emitted naming the project ──────────

    def test_warning_emitted(self):
        self.assertTrue(
            any(SCRUM_KEY in line and "ACL INCOMPLETE" in line for line in self._logs),
            f"expected an ACL-incomplete warning for {SCRUM_KEY}; got {self._logs}",
        )

    # ── 3. Fail-closed: the scope exists but has zero grants ──────────

    def test_scrum_scope_present_but_ungranted(self):
        scope_id = f"jira:project:{SCRUM_KEY}"
        ids = {s["scope_id"] for s in self._artifact["scopes"]}
        self.assertIn(scope_id, ids)
        grants = [sp for sp in self._artifact["scope_principals"]
                  if sp["scope_id"] == scope_id]
        self.assertEqual(grants, [], "team-managed project must be fail-closed (no grants)")

    # ── 4. The healthy project in the same run is unaffected ──────────

    def test_good_project_still_granted(self):
        scope_id = f"jira:project:{GOOD_KEY}"
        grants = [sp for sp in self._artifact["scope_principals"]
                  if sp["scope_id"] == scope_id]
        principal_ids = {g["principal_id"] for g in grants}
        self.assertIn(f"jira:U{USER_ALICE}", principal_ids)

    def test_good_project_not_in_incomplete_list(self):
        keys = {p["project_key"] for p in self._artifact.get("acl_incomplete_projects", [])}
        self.assertNotIn(GOOD_KEY, keys)


if __name__ == "__main__":
    unittest.main()
