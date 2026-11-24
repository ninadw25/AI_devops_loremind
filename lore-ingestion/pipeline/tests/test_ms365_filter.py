"""Unit tests for is_outlook_noise() and is_ms_teams_noise().

Each noise signal has an explicit drop case and an explicit keep case so a
regression in either direction is caught. Test style mirrors test_jira_filter.py:
one unittest.TestCase class per signal, keep + drop as separate test methods.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.filter import is_outlook_noise, is_ms_teams_noise, run_outlook_filter, run_ms_teams_filter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _outlook(
    sender: str = "alice@corp.com",
    subject: str = "Q3 Planning",
    body: str = "Let's review the roadmap and align on priorities.",
    is_draft: bool = False,
    headers: list | None = None,
    raw_extra: dict | None = None,
) -> dict:
    raw: dict = {
        "conversation_id": "CONV-001",
        "isDraft": is_draft,
    }
    if headers is not None:
        raw["internetMessageHeaders"] = headers
    if raw_extra:
        raw.update(raw_extra)
    return {
        "source": "outlook_mail",
        "primary_actor": f"email:{sender}",
        "title": subject,
        "body": body,
        "raw": raw,
    }


def _teams(
    body: str = "Can everyone join the design review at 3 pm?",
    message_type: str = "message",
    is_bot: bool = False,
) -> dict:
    actors = [{"id": "teams:USER-001", "display_name": "Alice", "is_bot": is_bot}]
    return {
        "source": "ms_teams",
        "primary_actor": "teams:USER-001",
        "title": body[:80],
        "body": body,
        "actors": actors,
        "raw": {
            "team_id": "T-1",
            "channel_id": "C-1",
            "scope_id": "ms365:channel:C-1",
            "reply_to_id": None,
            "message_type": message_type,
        },
    }


def _jsonl(events: list[dict]) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


# ═════════════════════════════════════════════════════════════════════════════
# OUTLOOK MAIL — individual signal tests
# ═════════════════════════════════════════════════════════════════════════════

class TestOutlookDraftSignal(unittest.TestCase):

    def test_draft_dropped(self):
        ev = _outlook(is_draft=True)
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_draft")

    def test_non_draft_not_dropped_by_this_signal(self):
        ev = _outlook(is_draft=False)
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_draft")

    def test_missing_isDraft_field_treated_as_false(self):
        ev = _outlook()
        del ev["raw"]["isDraft"]
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_draft")


class TestOutlookNoreplyDomainSignal(unittest.TestCase):

    def test_noreply_prefix_dropped(self):
        ev = _outlook(sender="noreply@corp.com")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_no_reply_hyphen_dropped(self):
        ev = _outlook(sender="no-reply@services.example.com")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_notifications_prefix_dropped(self):
        ev = _outlook(sender="notifications@github.com")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_mailer_daemon_dropped(self):
        ev = _outlook(sender="mailer-daemon@smtp.corp.com")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_bounce_prefix_dropped(self):
        ev = _outlook(sender="bounces.12345@em.sendgrid.net")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_human_sender_not_dropped(self):
        ev = _outlook(sender="alice@corp.com")
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_noreply_domain")

    def test_sender_with_noreply_prefix_in_local_part_dropped(self):
        # local part "noreply.alerts" starts with "noreply" → dropped correctly
        ev = _outlook(sender="noreply.alerts@corp.com")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_noreply_domain")

    def test_sender_with_noreply_not_as_prefix_kept(self):
        # "alerts.noreply" — "noreply" is not the prefix, it's a suffix label
        # The local part is "alerts.noreply"; normalised starts-with check
        # against "noreply" prefix: "alertsnoreply".startswith("noreply") == False
        ev = _outlook(sender="alerts.noreply@corp.com")
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_noreply_domain")


class TestOutlookAutoSubjectSignal(unittest.TestCase):

    def test_out_of_office_dropped(self):
        ev = _outlook(subject="Out of Office: back on Monday")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_autoreply_dropped(self):
        ev = _outlook(subject="AutoReply: your message was received")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_automatic_reply_dropped(self):
        ev = _outlook(subject="Automatic Reply: I am on leave")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_delivery_report_dropped(self):
        ev = _outlook(subject="Delivery Report for message ENG-123")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_undeliverable_dropped(self):
        ev = _outlook(subject="Undeliverable: Q3 Planning")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_case_insensitive_match(self):
        ev = _outlook(subject="OUT OF OFFICE: Holiday")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_auto_subject")

    def test_normal_subject_kept(self):
        ev = _outlook(subject="Q3 Budget Review — final numbers")
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_auto_subject")


class TestOutlookBulkMailSignal(unittest.TestCase):

    def test_list_unsubscribe_header_dict_dropped(self):
        headers = [
            {"name": "X-Mailer", "value": "SendGrid"},
            {"name": "List-Unsubscribe", "value": "<mailto:unsub@example.com>"},
        ]
        ev = _outlook(headers=headers)
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_bulk_mail")

    def test_list_unsubscribe_header_case_insensitive(self):
        headers = [{"name": "list-unsubscribe", "value": "<https://example.com/unsub>"}]
        ev = _outlook(headers=headers)
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_bulk_mail")

    def test_list_unsubscribe_as_string_dropped(self):
        ev = _outlook(raw_extra={"internetMessageHeaders": "List-Unsubscribe: <mailto:unsub@x.com>"})
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_bulk_mail")

    def test_no_list_unsubscribe_not_dropped_by_this_signal(self):
        headers = [{"name": "X-Mailer", "value": "Outlook"}]
        ev = _outlook(headers=headers)
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_bulk_mail")

    def test_no_headers_not_dropped(self):
        ev = _outlook()   # no internetMessageHeaders key
        noise, reason = is_outlook_noise(ev)
        self.assertNotEqual(reason, "outlook_bulk_mail")


class TestOutlookEmptyBodySignal(unittest.TestCase):

    def test_empty_body_dropped(self):
        ev = _outlook(body="")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_empty_body")

    def test_html_only_body_dropped(self):
        ev = _outlook(body="<p></p>  <br/>")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_empty_body")

    def test_body_under_5_chars_dropped(self):
        ev = _outlook(body="Hi")
        noise, reason = is_outlook_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "outlook_empty_body")

    def test_body_exactly_5_chars_kept(self):
        ev = _outlook(body="Hello")
        noise, _ = is_outlook_noise(ev)
        self.assertFalse(noise)

    def test_substantive_body_kept(self):
        ev = _outlook(body="We should ship the new API by end of quarter.")
        noise, _ = is_outlook_noise(ev)
        self.assertFalse(noise)


class TestOutlookValidHumanEmail(unittest.TestCase):
    """A typical internal human email passes all signals."""

    def test_valid_human_email_kept(self):
        ev = _outlook(
            sender="bob@corp.com",
            subject="Re: Sprint 12 retrospective",
            body="I think we need to invest more in test coverage. Let me share some metrics.",
        )
        noise, reason = is_outlook_noise(ev)
        self.assertFalse(noise)
        self.assertEqual(reason, "")


class TestOutlookSignalPriority(unittest.TestCase):
    """Draft check fires before all other signals."""

    def test_draft_takes_priority_over_noreply_domain(self):
        ev = _outlook(sender="noreply@corp.com", is_draft=True)
        _, reason = is_outlook_noise(ev)
        self.assertEqual(reason, "outlook_draft")

    def test_noreply_takes_priority_over_auto_subject(self):
        ev = _outlook(sender="noreply@corp.com", subject="Out of Office")
        _, reason = is_outlook_noise(ev)
        self.assertEqual(reason, "outlook_noreply_domain")


# ═════════════════════════════════════════════════════════════════════════════
# MICROSOFT TEAMS — individual signal tests
# ═════════════════════════════════════════════════════════════════════════════

class TestTeamsSystemEventSignal(unittest.TestCase):

    def test_system_event_dropped(self):
        ev = _teams(message_type="systemEventMessage")
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_system_event")

    def test_regular_message_not_dropped_by_this_signal(self):
        ev = _teams(message_type="message")
        noise, reason = is_ms_teams_noise(ev)
        self.assertNotEqual(reason, "teams_system_event")

    def test_unknown_message_type_not_dropped_as_system(self):
        ev = _teams(message_type="unknownFutureType")
        noise, reason = is_ms_teams_noise(ev)
        self.assertNotEqual(reason, "teams_system_event")


class TestTeamsBotSenderSignal(unittest.TestCase):

    def test_bot_actor_dropped(self):
        ev = _teams(is_bot=True)
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_bot_sender")

    def test_human_actor_not_dropped(self):
        ev = _teams(is_bot=False)
        noise, reason = is_ms_teams_noise(ev)
        self.assertNotEqual(reason, "teams_bot_sender")

    def test_no_actors_list_not_dropped_by_bot_signal(self):
        ev = _teams()
        ev["actors"] = []
        noise, reason = is_ms_teams_noise(ev)
        self.assertNotEqual(reason, "teams_bot_sender")


class TestTeamsEmptyBodySignal(unittest.TestCase):

    def test_empty_body_dropped(self):
        ev = _teams(body="")
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_empty_body")

    def test_html_only_body_dropped(self):
        ev = _teams(body="<p></p>")
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_empty_body")

    def test_body_under_5_chars_dropped(self):
        ev = _teams(body="Ok")
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_empty_body")

    def test_body_exactly_5_chars_kept(self):
        ev = _teams(body="Done!")
        noise, _ = is_ms_teams_noise(ev)
        self.assertFalse(noise)

    def test_short_but_valid_message_kept(self):
        ev = _teams(body="LGTM.")
        noise, _ = is_ms_teams_noise(ev)
        self.assertFalse(noise)


class TestTeamsEmojiOnlySignal(unittest.TestCase):

    def test_single_emoji_dropped(self):
        ev = _teams(body="\U0001F44D")    # 👍
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_emoji_only")

    def test_multiple_emoji_dropped(self):
        ev = _teams(body="\U0001F600\U0001F4AF\U0001F525")   # 😀💯🔥
        noise, reason = is_ms_teams_noise(ev)
        self.assertTrue(noise)
        self.assertEqual(reason, "teams_emoji_only")

    def test_emoji_with_text_kept(self):
        ev = _teams(body="\U0001F44D Looks great, approved!")
        noise, _ = is_ms_teams_noise(ev)
        self.assertFalse(noise)

    def test_plain_text_not_emoji_only(self):
        ev = _teams(body="Will join the call in 5 minutes.")
        noise, reason = is_ms_teams_noise(ev)
        self.assertNotEqual(reason, "teams_emoji_only")


class TestTeamsValidDiscussionMessage(unittest.TestCase):
    """A typical substantive Teams message passes all signals."""

    def test_valid_discussion_kept(self):
        ev = _teams(
            body="I've reviewed the PR and left comments. The main concern is the "
                 "database migration — we should add a rollback plan before merging.",
            message_type="message",
            is_bot=False,
        )
        noise, reason = is_ms_teams_noise(ev)
        self.assertFalse(noise)
        self.assertEqual(reason, "")


class TestTeamsSignalPriority(unittest.TestCase):
    """system_event fires before bot, bot fires before empty body."""

    def test_system_event_priority_over_bot(self):
        ev = _teams(message_type="systemEventMessage", is_bot=True)
        _, reason = is_ms_teams_noise(ev)
        self.assertEqual(reason, "teams_system_event")

    def test_bot_priority_over_empty_body(self):
        ev = _teams(body="", is_bot=True)
        _, reason = is_ms_teams_noise(ev)
        self.assertEqual(reason, "teams_bot_sender")

    def test_empty_body_priority_over_emoji_only(self):
        # An empty body registers as empty_body before emoji_only regex runs
        ev = _teams(body="")
        _, reason = is_ms_teams_noise(ev)
        self.assertEqual(reason, "teams_empty_body")


# ═════════════════════════════════════════════════════════════════════════════
# run_outlook_filter() integration test
# ═════════════════════════════════════════════════════════════════════════════

class TestRunOutlookFilter(unittest.TestCase):

    def _write(self, events: list[dict], path: Path) -> None:
        path.write_text(_jsonl(events), encoding="utf-8")

    def test_filter_drops_noise_and_keeps_valid(self):
        events = [
            _outlook(body="Let's finalise the architecture before Friday."),    # KEEP
            _outlook(sender="noreply@alerts.corp.com", body="Alert triggered"), # DROP: noreply
            _outlook(subject="Out of Office: back Monday", body="Auto reply."), # DROP: auto_subject
            _outlook(is_draft=True, body="Draft thoughts"),                     # DROP: draft
            _outlook(body="Hi"),                                                 # DROP: empty_body
            _outlook(body="The deployment succeeded and the API is live."),      # KEEP
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_outlook_mail.jsonl"
            self._write(events, inp)

            stats = run_outlook_filter(inp, td, org="test", allow_empty=True)

        self.assertEqual(stats["total_input"], 6)
        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 4)
        self.assertEqual(stats["noise_reasons"]["outlook_noreply_domain"], 1)
        self.assertEqual(stats["noise_reasons"]["outlook_auto_subject"], 1)
        self.assertEqual(stats["noise_reasons"]["outlook_draft"], 1)
        self.assertEqual(stats["noise_reasons"]["outlook_empty_body"], 1)

    def test_output_file_contains_only_kept_events(self):
        events = [
            _outlook(subject="API design", body="We agreed to use REST over GraphQL."),
            _outlook(is_draft=True, body="Draft notes"),
            _outlook(subject="Re: API design", body="Agreed, let's document this."),
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_outlook_mail.jsonl"
            self._write(events, inp)

            run_outlook_filter(inp, td, org="test", allow_empty=True)

            out = td / "filtered" / "test_outlook_mail_filtered.jsonl"
            self.assertTrue(out.exists())
            kept = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]

        self.assertEqual(len(kept), 2)
        subjects = {ev["title"] for ev in kept}
        self.assertIn("API design", subjects)
        self.assertIn("Re: API design", subjects)

    def test_all_valid_events_kept(self):
        events = [
            _outlook(body="Budget approved for next quarter."),
            _outlook(body="We need to fix the authentication bug before release."),
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_outlook_mail.jsonl"
            self._write(events, inp)
            stats = run_outlook_filter(inp, td, org="test", allow_empty=True)

        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 0)


# ═════════════════════════════════════════════════════════════════════════════
# run_ms_teams_filter() integration test
# ═════════════════════════════════════════════════════════════════════════════

class TestRunMsTeamsFilter(unittest.TestCase):

    def _write(self, events: list[dict], path: Path) -> None:
        path.write_text(_jsonl(events), encoding="utf-8")

    def test_filter_drops_noise_and_keeps_valid(self):
        events = [
            _teams(body="Should we move the standup to 9 am?"),                        # KEEP
            _teams(message_type="systemEventMessage", body="Alice joined the channel"), # DROP: system
            _teams(is_bot=True, body="CI pipeline passed all tests."),                  # DROP: bot
            _teams(body="\U0001F44D"),                                                  # DROP: emoji_only
            _teams(body="Ok"),                                                           # DROP: empty_body
            _teams(body="I finished the refactor. Ready for review."),                  # KEEP
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_ms_teams.jsonl"
            self._write(events, inp)

            stats = run_ms_teams_filter(inp, td, org="test", allow_empty=True)

        self.assertEqual(stats["total_input"], 6)
        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 4)
        self.assertEqual(stats["noise_reasons"]["teams_system_event"], 1)
        self.assertEqual(stats["noise_reasons"]["teams_bot_sender"], 1)
        self.assertEqual(stats["noise_reasons"]["teams_emoji_only"], 1)
        self.assertEqual(stats["noise_reasons"]["teams_empty_body"], 1)

    def test_output_file_contains_only_kept_events(self):
        events = [
            _teams(body="Let's review the database schema changes."),
            _teams(message_type="systemEventMessage", body="Bob left the channel."),
            _teams(body="The schema looks clean to me. +1 to merge."),
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_ms_teams.jsonl"
            self._write(events, inp)

            run_ms_teams_filter(inp, td, org="test", allow_empty=True)

            out = td / "filtered" / "test_ms_teams_filtered.jsonl"
            self.assertTrue(out.exists())
            kept = [json.loads(l) for l in out.read_text().splitlines() if l.strip()]

        self.assertEqual(len(kept), 2)

    def test_all_valid_events_kept(self):
        events = [
            _teams(body="The performance regression was caused by the N+1 query."),
            _teams(body="Fixed. The query now uses a join instead."),
        ]
        with tempfile.TemporaryDirectory() as d:
            td = Path(d)
            (td / "raw").mkdir()
            inp = td / "raw" / "test_ms_teams.jsonl"
            self._write(events, inp)
            stats = run_ms_teams_filter(inp, td, org="test", allow_empty=True)

        self.assertEqual(stats["kept"], 2)
        self.assertEqual(stats["dropped"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
