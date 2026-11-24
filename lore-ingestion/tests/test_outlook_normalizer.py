"""Unit tests for infra/outlook/normalizer.py — Outlook Mail and Teams normalizers."""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from infra.outlook.normalizer import normalize_outlook_message, normalize_teams_message
from common.hashing import make_id
from common.schemas import EventType


# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def outlook_msg():
    return {
        "id": "MSG001",
        "conversationId": "CONV001",
        "subject": "Q3 Budget Review",
        "receivedDateTime": "2026-06-01T10:00:00Z",
        "from": {
            "emailAddress": {
                "name": "Alice Smith",
                "address": "Alice@TestOrg.com",
            }
        },
        "toRecipients": [
            {"emailAddress": {"name": "Bob Jones", "address": "bob@testorg.com"}},
        ],
        "ccRecipients": [
            {"emailAddress": {"name": "Carol Lee", "address": "carol@testorg.com"}},
        ],
        "bccRecipients": [],
        "body": {
            "contentType": "html",
            "content": "<html><body><p>Please review the Q3 budget.</p></body></html>",
        },
        "internetMessageId": "<msg001@testorg.com>",
        "categories": ["Finance"],
    }


@pytest.fixture
def teams_msg():
    return {
        "id": "TMSG001",
        "createdDateTime": "2026-06-01T11:00:00Z",
        "from": {
            "user": {
                "id": "USER-GUID-001",
                "displayName": "Alice Smith",
            }
        },
        "body": {
            "contentType": "html",
            "content": "<p>Deployment is scheduled for tonight.</p>",
        },
        "channelIdentity": {
            "teamId": "TEAM-001",
            "channelId": "CHAN-001",
        },
        "webUrl": "https://teams.microsoft.com/msg/001",
        "replyToId": None,
        "messageType": "message",
    }


@pytest.fixture
def teams_reply():
    return {
        "id": "TMSG002",
        "createdDateTime": "2026-06-01T11:05:00Z",
        "from": {
            "user": {
                "id": "USER-GUID-002",
                "displayName": "Bob Jones",
            }
        },
        "body": {
            "contentType": "text",
            "content": "Acknowledged.",
        },
        "channelIdentity": {
            "teamId": "TEAM-001",
            "channelId": "CHAN-001",
        },
        "webUrl": "https://teams.microsoft.com/msg/002",
        "replyToId": "TMSG001",
        "messageType": "message",
    }


# ─── Outlook Mail normalizer ──────────────────────────────────────────────────

class TestOutlookEventType:
    def test_event_type_is_email_message(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.event_type == EventType.EMAIL_MESSAGE

    def test_source_is_outlook_mail(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.source == "outlook_mail"


class TestOutlookId:
    def test_id_is_deterministic(self, outlook_msg):
        ev1 = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        ev2 = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev1.id == ev2.id

    def test_id_derived_from_message_id(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.id == make_id("outlook_mail", "MSG001")


class TestOutlookContent:
    def test_title_equals_subject(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.title == "Q3 Budget Review"

    def test_missing_subject_defaults_to_placeholder(self, outlook_msg):
        del outlook_msg["subject"]
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.title == "(no subject)"

    def test_empty_subject_passes_through(self, outlook_msg):
        outlook_msg["subject"] = ""
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.title == ""

    def test_html_stripped_from_body(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "<html>" not in ev.body
        assert "<body>" not in ev.body
        assert "<p>" not in ev.body
        assert "Please review the Q3 budget." in ev.body

    def test_body_truncated_at_4000_chars(self, outlook_msg):
        outlook_msg["body"]["content"] = "A" * 5000
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert len(ev.body) == 4000

    def test_body_under_4000_not_truncated(self, outlook_msg):
        outlook_msg["body"]["content"] = "Short body."
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.body == "Short body."

    def test_timestamp_preserved(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.timestamp == "2026-06-01T10:00:00Z"


class TestOutlookActors:
    def test_primary_actor_is_sender(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.primary_actor == "email:alice@testorg.com"

    def test_sender_email_lowercased(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "email:alice@testorg.com" in [a["id"] for a in ev.actors]

    def test_actors_include_to_recipients(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "email:bob@testorg.com" in [a["id"] for a in ev.actors]

    def test_actors_include_cc_recipients(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "email:carol@testorg.com" in [a["id"] for a in ev.actors]

    def test_actor_ids_use_email_prefix(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        for actor in ev.actors:
            assert actor["id"].startswith("email:")

    def test_actor_source_is_outlook_mail(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        for actor in ev.actors:
            assert actor["source"] == "outlook_mail"

    def test_no_duplicate_actors(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        ids = [a["id"] for a in ev.actors]
        assert len(ids) == len(set(ids))


class TestOutlookParentId:
    def test_parent_id_derived_from_conversation_id(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        expected = make_id("outlook_mail.thread", "CONV001")
        assert ev.parent_id == expected

    def test_parent_id_none_when_no_conversation_id(self, outlook_msg):
        outlook_msg["conversationId"] = ""
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.parent_id is None

    def test_parent_id_consistent_across_same_thread(self, outlook_msg):
        msg2 = dict(outlook_msg)
        msg2["id"] = "MSG002"
        ev1 = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        ev2 = normalize_outlook_message(msg2, "testorg", "alice@testorg.com")
        assert ev1.parent_id == ev2.parent_id


class TestOutlookTags:
    def test_tags_always_contain_email(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "email" in ev.tags

    def test_internal_tag_when_sender_domain_matches(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "internal" in ev.tags
        assert "external" not in ev.tags

    def test_external_tag_when_sender_domain_differs(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "admin@otherdomain.com")
        assert "external" in ev.tags
        assert "internal" not in ev.tags


class TestOutlookProjectAndOrg:
    def test_project_is_subject(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.project == "Q3 Budget Review"

    def test_org_field_set(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.org == "testorg"

    def test_url_contains_message_id(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "MSG001" in ev.url


class TestOutlookRawPayload:
    def test_raw_has_required_keys(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "conversation_id" in ev.raw
        assert "internet_message_id" in ev.raw
        assert "body_content_type" in ev.raw
        assert "categories" in ev.raw

    def test_raw_conversation_id_value(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.raw["conversation_id"] == "CONV001"

    def test_raw_does_not_contain_full_body_content(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert "content" not in ev.raw

    def test_raw_categories(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.raw["categories"] == ["Finance"]

    def test_raw_body_content_type(self, outlook_msg):
        ev = normalize_outlook_message(outlook_msg, "testorg", "alice@testorg.com")
        assert ev.raw["body_content_type"] == "html"


# ─── Teams normalizer ─────────────────────────────────────────────────────────

class TestTeamsEventType:
    def test_event_type_is_team_message(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.event_type == EventType.TEAM_MESSAGE

    def test_source_is_ms_teams(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.source == "ms_teams"


class TestTeamsId:
    def test_id_is_deterministic(self, teams_msg):
        ev1 = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        ev2 = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev1.id == ev2.id

    def test_id_includes_channel_identity(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        expected = make_id("ms_teams", "testorg:TEAM-001:CHAN-001:TMSG001")
        assert ev.id == expected


class TestTeamsContent:
    def test_html_stripped_from_body(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert "<p>" not in ev.body
        assert "Deployment is scheduled for tonight." in ev.body

    def test_body_truncated_at_4000_chars(self, teams_msg):
        teams_msg["body"]["content"] = "C" * 5000
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert len(ev.body) <= 4000

    def test_title_derived_from_body_first_100_chars(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert len(ev.title) <= 100
        assert "Deployment" in ev.title

    def test_timestamp_preserved(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.timestamp == "2026-06-01T11:00:00Z"

    def test_url_preserved(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.url == "https://teams.microsoft.com/msg/001"


class TestTeamsActors:
    def test_primary_actor_id(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.primary_actor == "teams:USER-GUID-001"

    def test_actor_id_uses_teams_prefix(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.actors[0]["id"] == "teams:USER-GUID-001"

    def test_actor_source_is_ms_teams(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.actors[0]["source"] == "ms_teams"

    def test_actor_display_name(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.actors[0]["display_name"] == "Alice Smith"

    def test_one_actor_for_single_sender(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert len(ev.actors) == 1


class TestTeamsParentId:
    def test_no_parent_id_for_root_message(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.parent_id is None

    def test_parent_id_set_for_reply(self, teams_reply):
        ev = normalize_teams_message(teams_reply, "Engineering", "general", "testorg")
        assert ev.parent_id is not None

    def test_parent_id_value_for_reply(self, teams_reply):
        ev = normalize_teams_message(teams_reply, "Engineering", "general", "testorg")
        expected = make_id("ms_teams", "testorg:TEAM-001:CHAN-001:TMSG001")
        assert ev.parent_id == expected

    def test_parent_id_points_to_root_message(self, teams_msg, teams_reply):
        root = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        reply = normalize_teams_message(teams_reply, "Engineering", "general", "testorg")
        assert reply.parent_id == root.id


class TestTeamsTags:
    def test_tags_contain_teams(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert "teams" in ev.tags

    def test_tags_contain_team_id(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert "team:TEAM-001" in ev.tags


class TestTeamsProjectAndOrg:
    def test_project_combines_team_and_channel(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.project == "Engineering#general"

    def test_org_field_set(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.org == "testorg"


class TestTeamsRawPayload:
    def test_raw_has_required_keys(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert "team_id" in ev.raw
        assert "channel_id" in ev.raw
        assert "scope_id" in ev.raw
        assert "reply_to_id" in ev.raw
        assert "message_type" in ev.raw

    def test_raw_team_and_channel_ids(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.raw["team_id"] == "TEAM-001"
        assert ev.raw["channel_id"] == "CHAN-001"

    def test_raw_reply_to_id_none_for_root(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.raw["reply_to_id"] is None

    def test_raw_reply_to_id_set_for_reply(self, teams_reply):
        ev = normalize_teams_message(teams_reply, "Engineering", "general", "testorg")
        # reply_to_id is the composite source_id of the root so group_by_thread()
        # can match replies to their root with a single key comparison.
        assert ev.raw["reply_to_id"] == "testorg:TEAM-001:CHAN-001:TMSG001"

    def test_raw_message_type(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.raw["message_type"] == "message"

    def test_raw_does_not_contain_full_body(self, teams_msg):
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert "content" not in ev.raw

    def test_raw_scope_id_ms365_channel_format(self, teams_msg):
        """scope_id must be stamped as ms365:channel:{channelId} — locked format."""
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.raw["scope_id"] == "ms365:channel:CHAN-001"

    def test_raw_scope_id_matches_channel_id(self, teams_reply):
        """scope_id is consistent for both root and reply messages in the same channel."""
        ev = normalize_teams_message(teams_reply, "Engineering", "general", "testorg")
        assert ev.raw["scope_id"] == "ms365:channel:CHAN-001"

    def test_raw_scope_id_none_when_no_channel_id(self, teams_msg):
        """scope_id is None when channelIdentity is absent (defensive)."""
        teams_msg.pop("channelIdentity", None)
        ev = normalize_teams_message(teams_msg, "Engineering", "general", "testorg")
        assert ev.raw["scope_id"] is None
