"""Tests for is_jira_noise(), load_jira_bot_ids(), and run_jira_filter().

Each noise signal is tested with a keep and a drop case.
Bot detection covers both the actors[] path (comments) and the
primary_actor cross-reference path (issues with actors=[]).
group_by_thread scope hoisting is tested in TestScopeHoisting.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.filter import is_jira_noise, load_jira_bot_ids, run_jira_filter
from pipeline.extraction.grouping import group_by_thread


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(
    event_type: str = "jira.issue.created",
    body: str = "We decided to migrate the database to Postgres.",
    actors: list | None = None,
    source_ref: str = "ENG-1",
) -> dict:
    return {
        "source": "jira",
        "source_ref": source_ref,
        "event_type": event_type,
        "body": body,
        "title": "Migrate DB",
        "actors": actors if actors is not None else [],
        "scope_id": "jira:project:ENG",
        "extra_scope_ids": [],
    }


def _comment(
    body: str = "Looks good to me, let's proceed.",
    actor_type: str = "atlassian",
    source_ref: str = "ENG-1",
) -> dict:
    return {
        "source": "jira",
        "source_ref": source_ref,
        "event_type": "jira.comment.added",
        "body": body,
        "title": "Re: Migrate DB",
        "actors": [{"accountId": "abc", "accountType": actor_type}],
    }


def _changelog(field: str = "status") -> dict:
    return {
        "source": "jira",
        "source_ref": "ENG-1",
        "event_type": "jira.issue.transitioned" if field == "status" else "jira.issue.reassigned",
        "body": f"{{'field': '{field}', 'fromString': 'To Do', 'toString': 'In Progress'}}",
        "title": f"{field.title()}: To Do → In Progress",
        "actors": [],
    }


# ---------------------------------------------------------------------------
# Signal 1: Bot actor
# ---------------------------------------------------------------------------

class TestBotActorSignal(unittest.TestCase):

    def test_app_actor_dropped(self):
        ev = _comment(actor_type="app")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_bot_actor")

    def test_atlassian_actor_kept(self):
        ev = _comment(actor_type="atlassian")
        noise, _ = is_jira_noise(ev)
        self.assertFalse(noise)

    def test_no_actors_not_dropped_by_bot_signal(self):
        ev = _issue(actors=[])
        noise, reason = is_jira_noise(ev)
        # May be kept or dropped for another reason — but NOT jira_bot_actor
        self.assertNotEqual(reason, "jira_bot_actor")

    def test_customer_portal_actor_dropped(self):
        ev = _comment(actor_type="app")
        ev["actors"][0]["displayName"] = "Jira Service Management Widget"
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_bot_actor")


# ---------------------------------------------------------------------------
# Signal 2: Changelog events
# ---------------------------------------------------------------------------

class TestChangelogSignal(unittest.TestCase):

    def test_status_transition_dropped(self):
        ev = _changelog("status")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_changelog")

    def test_assignee_change_dropped(self):
        ev = _changelog("assignee")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_changelog")

    def test_regular_issue_not_dropped_as_changelog(self):
        ev = _issue(event_type="jira.issue.created")
        noise, reason = is_jira_noise(ev)
        self.assertNotEqual(reason, "jira_changelog")

    def test_comment_not_dropped_as_changelog(self):
        ev = _comment()
        noise, reason = is_jira_noise(ev)
        self.assertNotEqual(reason, "jira_changelog")

    def test_epic_not_dropped_as_changelog(self):
        ev = _issue(event_type="jira.epic.created")
        noise, reason = is_jira_noise(ev)
        self.assertNotEqual(reason, "jira_changelog")


# ---------------------------------------------------------------------------
# Signal 3: Empty body
# ---------------------------------------------------------------------------

class TestEmptyBodySignal(unittest.TestCase):

    def test_none_body_dropped(self):
        ev = _issue(body="")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_empty_body")

    def test_whitespace_body_dropped(self):
        ev = _issue(body="   \n\t  ")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_empty_body")

    def test_missing_body_field_dropped(self):
        ev = _issue()
        del ev["body"]
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_empty_body")

    def test_short_body_kept(self):
        ev = _issue(body="Fix this bug.")
        noise, _ = is_jira_noise(ev)
        self.assertFalse(noise)

    def test_rich_body_kept(self):
        ev = _issue(body="After the design review we agreed to use Redis for caching.")
        noise, _ = is_jira_noise(ev)
        self.assertFalse(noise)


# ---------------------------------------------------------------------------
# Signal 4: Automated comment body
# ---------------------------------------------------------------------------

class TestAutomatedCommentSignal(unittest.TestCase):

    def test_automation_rule_prefix_dropped(self):
        ev = _comment(body="Automation rule triggered: ticket moved to Done.")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")

    def test_automatically_moved_dropped(self):
        ev = _comment(body="This issue has been automatically transitioned to In Review.")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")

    def test_moved_by_automation_dropped(self):
        ev = _comment(body="Moved to Done by automation")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")

    def test_automated_tag_dropped(self):
        ev = _comment(body="[Automated] Linked issue ENG-42 to this ticket.")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")

    def test_jira_automation_prefix_dropped(self):
        ev = _comment(body="Jira Automation: closed duplicate ENG-10.")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")

    def test_human_comment_kept(self):
        ev = _comment(body="I reviewed the PR and it looks good. Approving now.")
        noise, _ = is_jira_noise(ev)
        self.assertFalse(noise)

    def test_comment_mentioning_automation_in_middle_kept(self):
        # "automation" in the middle of a sentence is not an automated comment
        ev = _comment(body="We need to set up automation for the deployment pipeline.")
        noise, _ = is_jira_noise(ev)
        self.assertFalse(noise)

    def test_case_insensitive_match(self):
        ev = _comment(body="AUTOMATION RULE triggered for this sprint.")
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_automated_comment")


# ---------------------------------------------------------------------------
# Signal priority order
# ---------------------------------------------------------------------------

class TestSignalPriority(unittest.TestCase):
    """Bot actor check runs before changelog check — both drop, but reason differs."""

    def test_bot_actor_takes_priority_over_empty_body(self):
        ev = _comment(actor_type="app", body="")
        _, reason = is_jira_noise(ev)
        self.assertEqual(reason, "jira_bot_actor")

    def test_changelog_checked_after_actor(self):
        # Changelog with human actor → dropped as changelog, not bot
        ev = _changelog("status")
        ev["actors"] = [{"accountId": "human-id", "accountType": "atlassian"}]
        noise, reason = is_jira_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_changelog")


# ---------------------------------------------------------------------------
# run_jira_filter() integration test
# ---------------------------------------------------------------------------

class TestRunJiraFilter(unittest.TestCase):

    def _make_jsonl(self, events: list[dict]) -> str:
        return "\n".join(json.dumps(ev) for ev in events) + "\n"

    def test_filter_drops_expected_and_keeps_rest(self):
        events = [
            _issue(body="We agreed to use Postgres for the new service."),   # KEEP
            _comment(actor_type="app"),                                       # DROP: bot
            _changelog("status"),                                             # DROP: changelog
            _issue(body=""),                                                  # DROP: empty
            _comment(body="Automation rule triggered: moved to Done."),       # DROP: auto
            _comment(body="Good work everyone, this is ready for review."),   # KEEP
        ]

        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            raw_dir = td / "raw"
            raw_dir.mkdir()
            input_path = raw_dir / "test_jira_2026-05-27.jsonl"
            input_path.write_text(self._make_jsonl(events), encoding="utf-8")

            stats = run_jira_filter(input_path, td, org="test", allow_empty=True)

        self.assertEqual(stats["total_input"], 6)
        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 4)
        self.assertEqual(stats["noise_reasons"]["jira_bot_actor"], 1)
        self.assertEqual(stats["noise_reasons"]["jira_changelog"], 1)
        self.assertEqual(stats["noise_reasons"]["jira_empty_body"], 1)
        self.assertEqual(stats["noise_reasons"]["jira_automated_comment"], 1)

    def test_output_file_contains_only_kept_events(self):
        events = [
            _issue(body="Deploy the API to staging.", source_ref="ENG-5"),
            _changelog("status"),
            _comment(body="Done, deployed at 14:30.", source_ref="ENG-5"),
        ]

        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            input_path = td / "raw" / "test_jira_2026-05-27.jsonl"
            input_path.write_text(self._make_jsonl(events), encoding="utf-8")

            run_jira_filter(input_path, td, org="test", allow_empty=True)

            output_path = td / "filtered" / "test_jira_filtered.jsonl"
            self.assertTrue(output_path.exists())
            kept_events = [json.loads(l) for l in output_path.read_text().splitlines() if l.strip()]

        self.assertEqual(len(kept_events), 2)
        refs = {ev["source_ref"] for ev in kept_events}
        self.assertEqual(refs, {"ENG-5"})
        types = {ev["event_type"] for ev in kept_events}
        self.assertNotIn("jira.issue.transitioned", types)

    def test_noise_file_written(self):
        events = [_changelog("status"), _issue(body="Real content here.")]

        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            input_path = td / "raw" / "test_jira_2026-05-27.jsonl"
            input_path.write_text(self._make_jsonl(events), encoding="utf-8")

            run_jira_filter(input_path, td, org="test", allow_empty=True)

            noise_path = td / "raw" / "test_jira_noise.jsonl"
            self.assertTrue(noise_path.exists())
            noise_events = [json.loads(l) for l in noise_path.read_text().splitlines() if l.strip()]

        self.assertEqual(len(noise_events), 1)
        self.assertEqual(noise_events[0]["_filter_reason"], "jira_changelog")

    def test_all_clean_events_returned(self):
        events = [
            _issue(body="Decision: use GraphQL for the public API."),
            _comment(body="Agreed, I'll update the spec today."),
        ]

        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            input_path = td / "raw" / "test_jira_2026-05-27.jsonl"
            input_path.write_text(self._make_jsonl(events), encoding="utf-8")

            stats = run_jira_filter(input_path, td, org="test", allow_empty=True)

        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 0)
        self.assertEqual(stats["drop_rate_pct"], 0.0)


# ---------------------------------------------------------------------------
# Bot detection via primary_actor cross-reference (issue events)
# ---------------------------------------------------------------------------

_SAMPLE_ARTIFACT = {
    "scopes": [],
    "principals": [
        {"principal_id": "jira:U712020:bot-uuid-aaa", "account_type": "app",
         "display_name": "Automation for Jira"},
        {"principal_id": "jira:U712020:bot-uuid-bbb", "account_type": "app",
         "display_name": "Slack"},
        {"principal_id": "jira:U712020:human-uuid", "account_type": "atlassian",
         "display_name": "Alice"},
    ],
    "scope_principals": [],
    "groups": [],
    "group_members": [],
}


class TestLoadJiraBotIds(unittest.TestCase):

    def _write_artifact(self, tmp: Path, data: dict) -> Path:
        jira_dir = tmp / "jira"
        jira_dir.mkdir(parents=True, exist_ok=True)
        p = jira_dir / "myorg_scope_principals.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return tmp

    def test_returns_only_app_account_ids(self):
        with tempfile.TemporaryDirectory() as d:
            artifact = (Path(d) / "jira" / "myorg_scope_principals.json")
            artifact.parent.mkdir()
            artifact.write_text(json.dumps(_SAMPLE_ARTIFACT), encoding="utf-8")
            bot_ids = load_jira_bot_ids(artifact)
        self.assertIn("712020:bot-uuid-aaa", bot_ids)
        self.assertIn("712020:bot-uuid-bbb", bot_ids)
        self.assertNotIn("712020:human-uuid", bot_ids)

    def test_absent_artifact_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            bot_ids = load_jira_bot_ids(Path(d) / "nonexistent.json")
        self.assertEqual(len(bot_ids), 0)


class TestBotActorViaIssueEvent(unittest.TestCase):
    """Issue events have actors=[] — bot detection falls back to primary_actor lookup."""

    BOT_IDS = frozenset(["712020:bot-uuid-aaa"])

    def test_bot_issue_dropped_via_primary_actor(self):
        ev = _issue(actors=[])
        ev["primary_actor"] = "712020:bot-uuid-aaa"
        noise, reason = is_jira_noise(ev, bot_account_ids=self.BOT_IDS)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_bot_actor")

    def test_human_issue_kept_even_without_actors(self):
        ev = _issue(actors=[])
        ev["primary_actor"] = "712020:human-uuid"
        noise, _ = is_jira_noise(ev, bot_account_ids=self.BOT_IDS)
        self.assertFalse(noise)

    def test_no_bot_ids_provided_does_not_crash(self):
        ev = _issue(actors=[])
        ev["primary_actor"] = "712020:bot-uuid-aaa"
        # Without the lookup set, issue-level bots pass through (known gap without artifact)
        noise, _ = is_jira_noise(ev, bot_account_ids=None)
        self.assertFalse(noise)

    def test_comment_bot_still_caught_by_actors_dict(self):
        ev = _comment(actor_type="app")
        noise, reason = is_jira_noise(ev, bot_account_ids=self.BOT_IDS)
        self.assertTrue(noise)
        self.assertEqual(reason, "jira_bot_actor")


# ---------------------------------------------------------------------------
# scope_id / extra_scope_ids hoisted by group_by_thread
# ---------------------------------------------------------------------------

class TestScopeHoisting(unittest.TestCase):
    """group_by_thread must carry scope_id + extra_scope_ids to the thread dict."""

    def _write_jsonl(self, events: list[dict], path: Path) -> None:
        path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

    def test_scope_id_hoisted_from_root_issue(self):
        events = [
            {
                "id": "i1", "source": "jira", "source_id": "ENG-17",
                "event_type": "jira.issue.created", "title": "Migrate DB",
                "body": "We should migrate to Postgres.",
                "scope_id": "jira:project:ENG",
                "extra_scope_ids": ["jira:seclevel:10000"],
                "parent_id": None, "timestamp": "2026-05-01T10:00:00Z",
            },
            {
                "id": "c1", "source": "jira", "source_id": "ENG-17#comment-1",
                "event_type": "jira.comment.added", "title": "Re: Migrate DB",
                "body": "Agreed, let's do it.",
                "parent_id": "ENG-17", "timestamp": "2026-05-01T11:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "filtered.jsonl"
            self._write_jsonl(events, p)
            threads = group_by_thread(p)

        self.assertIn("ENG-17", threads)
        t = threads["ENG-17"]
        self.assertEqual(t["scope_id"], "jira:project:ENG")
        self.assertEqual(t["extra_scope_ids"], ["jira:seclevel:10000"])
        self.assertEqual(len(t["emails"]), 2)

    def test_unrestricted_issue_empty_extra_scope_ids(self):
        events = [
            {
                "id": "i2", "source": "jira", "source_id": "ENG-25",
                "event_type": "jira.issue.created", "title": "Add feature",
                "body": "Add dark mode to the dashboard.",
                "scope_id": "jira:project:ENG",
                "extra_scope_ids": [],
                "parent_id": None, "timestamp": "2026-05-02T09:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "filtered.jsonl"
            self._write_jsonl(events, p)
            threads = group_by_thread(p)

        t = threads["ENG-25"]
        self.assertEqual(t["scope_id"], "jira:project:ENG")
        self.assertEqual(t["extra_scope_ids"], [])

    def test_gmail_thread_has_no_scope_id(self):
        events = [
            {
                "id": "g1", "source": "gmail", "source_id": "thread-abc",
                "event_type": "email", "title": "Q4 planning",
                "body": "Let's discuss the roadmap.",
                "parent_id": None, "timestamp": "2026-05-01T08:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "filtered.jsonl"
            self._write_jsonl(events, p)
            threads = group_by_thread(p)

        t = threads["thread-abc"]
        self.assertNotIn("scope_id", t)
        self.assertNotIn("extra_scope_ids", t)

    def test_source_ref_and_url_hoisted_from_root_issue(self):
        events = [
            {
                "id": "i4", "source": "jira", "source_id": "ENG-99",
                "event_type": "jira.issue.created", "title": "Deploy service",
                "body": "Deploy the new auth service.",
                "scope_id": "jira:project:ENG",
                "extra_scope_ids": [],
                "source_ref": "ENG-99",
                "source_url": "https://example.atlassian.net/browse/ENG-99",
                "project": "ENG",
                "parent_id": None, "timestamp": "2026-05-03T10:00:00Z",
            },
            {
                "id": "c3", "source": "jira", "source_id": "ENG-99#comment-1",
                "event_type": "jira.comment.added", "title": "Re: Deploy service",
                "body": "Looks good.",
                "parent_id": "ENG-99", "timestamp": "2026-05-03T11:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "filtered.jsonl"
            self._write_jsonl(events, p)
            threads = group_by_thread(p)

        t = threads["ENG-99"]
        self.assertEqual(t["source_ref"], "ENG-99")
        self.assertEqual(t["source_url"], "https://example.atlassian.net/browse/ENG-99")
        self.assertEqual(t["project"], "ENG")

    def test_scope_not_overwritten_by_later_comment(self):
        """A comment that somehow has scope_id must not overwrite the root's value."""
        events = [
            {
                "id": "i3", "source": "jira", "source_id": "HR-6",
                "event_type": "jira.issue.created", "title": "Review policy",
                "body": "Update the leave policy.",
                "scope_id": "jira:project:HR",
                "extra_scope_ids": ["jira:seclevel:10001"],
                "parent_id": None, "timestamp": "2026-05-01T10:00:00Z",
            },
            {
                "id": "c2", "source": "jira", "source_id": "HR-6#comment-2",
                "event_type": "jira.comment.added", "title": "Re: Review policy",
                "body": "Done.",
                "scope_id": "jira:project:HR",       # should not overwrite
                "extra_scope_ids": [],
                "parent_id": "HR-6", "timestamp": "2026-05-01T12:00:00Z",
            },
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "filtered.jsonl"
            self._write_jsonl(events, p)
            threads = group_by_thread(p)

        t = threads["HR-6"]
        # Must keep the extra_scope_ids from the first event that set scope_id
        self.assertEqual(t["extra_scope_ids"], ["jira:seclevel:10001"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
