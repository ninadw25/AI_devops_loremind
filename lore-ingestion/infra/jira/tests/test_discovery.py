"""Smoke test suite 1/4 — TestDiscoveryAllPhases.

Runs all 3 discovery phases against a mock fetcher (no real Jira API or
Neo4j needed). Two projects: ENG has user+group BROWSE grants, OPS has an
applicationRole grant. One security scheme with one level (Confidential),
with Alice as the sole member.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from infra.jira.discovery import run_discovery
from common.output_paths import JiraPaths

ORG         = "acme"
ENG_KEY     = "ENG"
OPS_KEY     = "OPS"
USER_ALICE  = "712020:alice-uuid"
USER_BOB    = "712020:bob-uuid"
GROUP_UUID  = "gg-eng-uuid"
SECLEVEL_ID = "10001"


async def _fake_get(endpoint, params=None):
    if "/users/search" in endpoint:
        return [
            {"accountId": USER_ALICE, "emailAddress": "alice@acme.io",
             "displayName": "Alice", "accountType": "atlassian"},
            {"accountId": USER_BOB,   "emailAddress": "bob@acme.io",
             "displayName": "Bob",   "accountType": "atlassian"},
        ]
    # check /group/member before /groups to avoid prefix collision
    if "/group/member" in endpoint:
        return {"values": [{"accountId": USER_ALICE}], "isLast": True}
    if "/group/bulk" in endpoint or "/groups/picker" in endpoint or "/groups" in endpoint:
        return {"values": [{"groupId": GROUP_UUID, "name": "eng-team"}]}
    if f"/project/{ENG_KEY}/role/" in endpoint or f"/project/{OPS_KEY}/role/" in endpoint:
        return {"actors": []}
    return {}


def _permission_scheme(project_key):
    return {"id": "100"} if project_key == ENG_KEY else {"id": "200"}


def _scheme_permissions(scheme_id):
    if str(scheme_id) == "100":
        return [
            {"permission": "BROWSE_PROJECTS",
             "holder": {"type": "user", "parameter": USER_ALICE}},
            {"permission": "BROWSE_PROJECTS",
             "holder": {"type": "group", "parameter": "eng-team"}},
        ]
    if str(scheme_id) == "200":
        return [{"permission": "BROWSE_PROJECTS",
                 "holder": {"type": "applicationRole"}}]
    return []


def _make_fetcher() -> AsyncMock:
    f = AsyncMock()
    f.get.side_effect = _fake_get
    f.list_projects.return_value = [
        {"key": ENG_KEY, "name": "Engineering"},
        {"key": OPS_KEY, "name": "Operations"},
    ]
    f.fetch_project_permission_scheme.side_effect    = _permission_scheme
    f.fetch_permission_scheme_permissions.side_effect = _scheme_permissions
    f.fetch_security_schemes.return_value = [{"id": "900", "name": "Default Scheme"}]
    f.fetch_security_scheme_levels.return_value = [
        {"id": SECLEVEL_ID, "name": "Confidential"},
    ]
    f.fetch_security_level_members.return_value = [
        {"holder": {"type": "user", "parameter": f"accountId:{USER_ALICE}"}},
    ]
    return f


class TestDiscoveryAllPhases(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._artifact = await run_discovery(
            ORG, _make_fetcher(), output_dir=Path(self._tmp.name)
        )

    def tearDown(self):
        self._tmp.cleanup()

    # ── 1. Artifact top-level shape ───────────────────────────────────

    def test_top_level_keys(self):
        required = {"scopes", "principals", "scope_principals", "groups", "group_members"}
        self.assertTrue(required.issubset(self._artifact.keys()))

    # ── 2. Both project scopes present ───────────────────────────────

    def test_eng_scope_present(self):
        ids = {s["scope_id"] for s in self._artifact["scopes"]}
        self.assertIn(f"jira:project:{ENG_KEY}", ids)

    def test_ops_scope_present(self):
        ids = {s["scope_id"] for s in self._artifact["scopes"]}
        self.assertIn(f"jira:project:{OPS_KEY}", ids)

    # ── 3. Security level scope added by phase 3 ─────────────────────

    def test_seclevel_scope_added(self):
        ids = {s["scope_id"] for s in self._artifact["scopes"]}
        self.assertIn(f"jira:seclevel:{SECLEVEL_ID}", ids)

    # ── 4. is_org_wide flag ──────────────────────────────────────────

    def test_eng_not_org_wide(self):
        eng = next(s for s in self._artifact["scopes"]
                   if s["scope_id"] == f"jira:project:{ENG_KEY}")
        self.assertFalse(eng["is_org_wide"])

    def test_ops_is_org_wide(self):
        ops = next(s for s in self._artifact["scopes"]
                   if s["scope_id"] == f"jira:project:{OPS_KEY}")
        self.assertTrue(ops["is_org_wide"])

    # ── 5. scope_principals grants ───────────────────────────────────

    def test_eng_has_user_and_group_grants(self):
        eng_rows    = [sp for sp in self._artifact["scope_principals"]
                       if sp["scope_id"] == f"jira:project:{ENG_KEY}"]
        grant_types = {r["grant_source"] for r in eng_rows}
        self.assertIn("jira_browse_user",  grant_types)
        self.assertIn("jira_browse_group", grant_types)

    def test_alice_has_seclevel_grant(self):
        alice_id    = f"jira:U{USER_ALICE}"
        seclevel_rows = [sp for sp in self._artifact["scope_principals"]
                         if sp["scope_id"] == f"jira:seclevel:{SECLEVEL_ID}"]
        principal_ids = {r["principal_id"] for r in seclevel_rows}
        self.assertIn(alice_id, principal_ids)

    # ── 6. Artifact file written to disk ─────────────────────────────

    def test_artifact_file_written(self):
        path = JiraPaths(ORG, data_dir=Path(self._tmp.name)).scope_principals
        self.assertTrue(path.exists(), f"artifact not found at {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("scopes", data)
        self.assertIn("scope_principals", data)
