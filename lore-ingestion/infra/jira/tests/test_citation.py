"""Smoke test suite 3/4 — TestCitation.

Verifies that source_ref and source_url are set correctly on normalized
issue and comment events, including trailing-slash stripping and the
absence of both fields when base_url is omitted.
"""
from __future__ import annotations

import re
import unittest

from infra.jira.normalizer import normalize_issue

ORG      = "acme"
ENG_KEY  = "ENG"
ISSUE_NO = "42"
BASE_URL = "https://acme.atlassian.net"


def _issue() -> dict:
    return {
        "key": f"{ENG_KEY}-{ISSUE_NO}",
        "fields": {
            "summary": "Deploy new auth service",
            "issuetype": {"name": "Task"},
            "project": {"key": ENG_KEY},
            "reporter": {"accountId": "alice-id"},
            "created": "2024-01-15T10:00:00.000+0000",
        },
    }


def _issue_with_comment() -> dict:
    issue = _issue()
    issue["fields"]["comment"] = {
        "comments": [{
            "id": "10100",
            "body": {"type": "text", "text": "Looks good to merge"},
            "author": {"accountId": "alice-id"},
            "created": "2024-01-15T11:00:00.000+0000",
        }]
    }
    return issue


class TestCitation(unittest.TestCase):

    def setUp(self):
        events = normalize_issue(_issue(), ORG, base_url=BASE_URL)
        self._event = events[0]

    # ── 1. source_ref format: {KEY}-{number} ─────────────────────────

    def test_source_ref(self):
        self.assertEqual(self._event["source_ref"], f"{ENG_KEY}-{ISSUE_NO}")

    def test_source_ref_matches_key_number_pattern(self):
        self.assertRegex(self._event["source_ref"], r"^[A-Z]+-\d+$")

    # ── 2. source_url exact form ─────────────────────────────────────

    def test_source_url_exact(self):
        self.assertEqual(
            self._event["source_url"],
            f"{BASE_URL}/browse/{ENG_KEY}-{ISSUE_NO}",
        )

    def test_source_url_starts_with_base_browse(self):
        self.assertTrue(self._event["source_url"].startswith(f"{BASE_URL}/browse/"))

    def test_trailing_slash_stripped(self):
        events = normalize_issue(_issue(), ORG, base_url=BASE_URL + "/")
        self.assertEqual(
            events[0]["source_url"],
            f"{BASE_URL}/browse/{ENG_KEY}-{ISSUE_NO}",
        )

    # ── 3. No citation fields when base_url is omitted ───────────────

    def test_no_citation_without_base_url(self):
        events = normalize_issue(_issue(), ORG, base_url=None)
        event  = events[0]
        self.assertNotIn("source_ref", event)
        self.assertNotIn("source_url", event)

    # ── 4. Comment source_url points to parent issue ─────────────────

    def test_comment_source_url_is_parent_issue(self):
        events  = normalize_issue(_issue_with_comment(), ORG, base_url=BASE_URL)
        comment = next(e for e in events if e.get("event_type") == "jira.comment.added")
        self.assertEqual(
            comment["source_url"],
            f"{BASE_URL}/browse/{ENG_KEY}-{ISSUE_NO}",
        )

    # ── 5. url field mirrors source_url on the issue event ───────────

    def test_url_field_equals_source_url(self):
        self.assertEqual(self._event.get("url"), self._event["source_url"])
