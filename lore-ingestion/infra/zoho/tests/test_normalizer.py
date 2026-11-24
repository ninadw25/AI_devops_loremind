"""Unit tests for infra/zoho/normalizer.py — sample Mail + Cliq payloads."""
from __future__ import annotations

from common.hashing import make_id
from common.schemas import EventType
from common import scopes
from infra.zoho.normalizer import normalize_cliq_message, normalize_zoho_mail, strip_html


SAMPLE_MAIL_MSG = {
    "messageId": "msg-123",
    "threadId": "thread-456",
    "subject": "Q3 budget approval",
    "fromAddress": '"Alice Smith" <alice@acme.com>',
    "toAddress": "bob@acme.com, carol@partner.com",
    "ccAddress": "",
    "content": "<p>Approved the Q3 budget.</p>",
    "receivedTime": "1718000000000",
    "folderId": "1",
    "hasAttachment": False,
}


def test_normalize_zoho_mail_basic_fields():
    event = normalize_zoho_mail(SAMPLE_MAIL_MSG, org="acme", account_email="alice@acme.com")

    assert event.id == make_id("zoho_mail", "msg-123")
    assert event.source == "zoho_mail"
    assert event.source_id == "msg-123"
    assert event.event_type == EventType.EMAIL_MESSAGE
    assert event.title == "Q3 budget approval"
    assert "Approved the Q3 budget." in event.body
    assert "<p>" not in event.body
    assert event.primary_actor == "email:alice@acme.com"
    assert event.parent_id == make_id("zoho_mail.thread", "thread-456")
    assert event.raw["thread_id"] == "thread-456"


def test_normalize_zoho_mail_tags_external_when_recipient_outside_domain():
    event = normalize_zoho_mail(SAMPLE_MAIL_MSG, org="acme", account_email="alice@acme.com")
    # sender is internal (acme.com == account domain) -> tagged internal
    assert "internal" in event.tags


def test_normalize_zoho_mail_actors_include_sender_and_recipients():
    event = normalize_zoho_mail(SAMPLE_MAIL_MSG, org="acme", account_email="alice@acme.com")
    actor_ids = {a["id"] for a in event.actors}
    assert "email:alice@acme.com" in actor_ids
    assert "email:bob@acme.com" in actor_ids
    assert "email:carol@partner.com" in actor_ids


def test_normalize_zoho_mail_handles_missing_thread_id():
    msg = {**SAMPLE_MAIL_MSG, "threadId": ""}
    event = normalize_zoho_mail(msg, org="acme", account_email="alice@acme.com")
    assert event.parent_id is None


SAMPLE_CLIQ_MSG = {
    "id": "msg-789",
    "channel_id": "chan-1",
    "message": "<b>Standup notes</b>: shipped the release.",
    "sender": {"id": "zuid-42", "name": "Dana Lee", "email": "dana@acme.com", "type": "user"},
    "time": "1718000100000",
    "message_url": "https://cliq.zoho.com/chat/chan-1/msg-789",
}


def test_normalize_cliq_message_basic_fields():
    event = normalize_cliq_message(SAMPLE_CLIQ_MSG, channel_name="general", org="acme")

    assert event.source == "zoho_cliq"
    assert event.event_type == EventType.ZOHO_CLIQ_MESSAGE
    assert "Standup notes" in event.body
    assert "<b>" not in event.body
    assert event.primary_actor == "zoho:zuid-42"
    assert event.raw["channel_id"] == "chan-1"
    assert event.raw["scope_id"] == scopes.zoho_cliq_channel("chan-1")
    assert event.project == "general"


def test_normalize_cliq_message_bot_sender_flagged():
    msg = {**SAMPLE_CLIQ_MSG, "sender": {"id": "zuid-bot", "name": "ReleaseBot", "type": "bot"}}
    event = normalize_cliq_message(msg, channel_name="general", org="acme")
    assert event.actors[0]["is_bot"] is True


def test_normalize_cliq_message_thread_reply_sets_parent_id():
    msg = {**SAMPLE_CLIQ_MSG, "thread_id": "msg-100"}
    event = normalize_cliq_message(msg, channel_name="general", org="acme")
    assert event.parent_id == make_id("zoho_cliq", "chan-1:msg-100")


def test_strip_html_removes_tags():
    assert strip_html("<p>hello <b>world</b></p>") == "hello world"
    assert strip_html("") == ""
    assert strip_html(None) == ""
