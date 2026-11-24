"""Normalizer for Outlook messages and Teams messages.
"""
from __future__ import annotations

import re
from typing import Any

from common import scopes
from common.hashing import make_id
from common.logger import get_logger
from common.schemas import Actor, EventType, KnowledgeEvent

log = get_logger("outlook.normalizer")

# ─── HTML Stripping ───────────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile("<[^>]+>")


def strip_html(text: str) -> str:
    """A basic regex-based HTML tag stripper.
    This is a fallback for when BeautifulSoup is not available.
    """
    if not text:
        return ""
    return _HTML_TAG_RE.sub("", text)


# ─── Outlook Mail Normalizer ──────────────────────────────────────────────────

def _actor_from_outlook_recipient(recipient: dict[str, Any]) -> Actor:
    """Creates an Actor from an Outlook recipient graph object."""
    email_addr_obj = recipient.get("emailAddress", {})
    name = email_addr_obj.get("name", "")
    email = email_addr_obj.get("address", "")
    if not name and email:
        name = email.split("@")[0]
    return Actor(
        id=f"email:{email.lower()}",
        display_name=name,
        email=email.lower(),
        source="outlook_mail",
    )


def normalize_outlook_message(
    msg: dict[str, Any], org: str, employee_email: str
) -> KnowledgeEvent:
    """Normalizes a single Outlook message into a KnowledgeEvent."""
    subject = msg.get("subject", "(no subject)")
    message_id = msg.get("id", "")
    conversation_id = msg.get("conversationId", "")

    sender_obj = msg.get("from") or msg.get("sender")
    sender = _actor_from_outlook_recipient(sender_obj) if sender_obj else None

    recipients = [_actor_from_outlook_recipient(r) for r in msg.get("toRecipients", [])]
    recipients.extend(
        _actor_from_outlook_recipient(r) for r in msg.get("ccRecipients", [])
    )
    recipients.extend(
        _actor_from_outlook_recipient(r) for r in msg.get("bccRecipients", [])
    )

    body_obj = msg.get("body", {})
    body_raw = body_obj.get("content", "")
    body_clean = strip_html(body_raw)

    all_actors = {sender.id: sender} if sender else {}
    for r in recipients:
        if r.id not in all_actors:
            all_actors[r.id] = r

    domain = employee_email.split("@")[-1] if "@" in employee_email else ""
    tags = ["email"]
    if domain and sender and sender.email and domain not in (sender.email or ""):
        tags.append("external")
    else:
        tags.append("internal")

    # Per LOR-14, construct a curated raw payload
    raw_payload = {
        "conversation_id": conversation_id,
        "internet_message_id": msg.get("internetMessageId"),
        "body_content_type": body_obj.get("contentType"),
        "categories": msg.get("categories", []),
    }

    return KnowledgeEvent(
        id=make_id("outlook_mail", message_id),
        source="outlook_mail",
        source_id=message_id,
        event_type=EventType.EMAIL_MESSAGE,
        timestamp=msg.get("receivedDateTime"),
        title=subject,
        body=body_clean[:4000],
        primary_actor=sender.id if sender else None,
        actors=[a.to_dict() for a in all_actors.values()],
        url=f"https://outlook.office.com/mail/{message_id}",
        org=org,
        project=subject,
        tags=tags,
        parent_id=make_id("outlook_mail.thread", conversation_id) if conversation_id else None,
        raw=raw_payload,
    )

# ─── Teams Message Normalizer ─────────────────────────────────────────────────

def _actor_from_teams_user(user: dict[str, Any] | None) -> Actor | None:
    """Creates an Actor from a Teams user graph object."""
    if not user:
        return None

    user_info = user.get("user") or user.get("application") or user
    
    # Graph gives us a `user` inside `from` that has id, displayName
    if 'user' in user:
        user_info = user['user']
        
    id_val = user_info.get("id", "")
    display_name = user_info.get("displayName", "Unknown")
    
    # Heuristic to create an email if not present
    email = None
    if 'email' in user_info and user_info['email']:
        email = user_info['email']
    elif 'id' in user_info and '@' not in user_info.get('displayName', ''):
        # This part is a guess, might need adjustment based on real data
        pass

    return Actor(
        id=f"teams:{id_val}",
        display_name=display_name,
        email=email,
        source="ms_teams",
        is_bot='application' in user
    )


def normalize_teams_message(
    msg: dict[str, Any], team_name: str, channel_name: str, org: str
) -> KnowledgeEvent:
    """Normalizes a single Teams message into a KnowledgeEvent."""
    message_id = msg.get("id", "")
    
    body_obj = msg.get("body", {})
    body_raw = body_obj.get("content", "")
    body_clean = strip_html(body_raw)

    title = body_clean[:100]

    author = _actor_from_teams_user(msg.get("from"))
    
    actors = [author.to_dict()] if author else []

    channel_identity = msg.get("channelIdentity")
    parent_id = None
    if msg.get("replyToId"):
        if channel_identity:
            parent_id = make_id(
                "ms_teams",
                f"{org}:{channel_identity.get('teamId')}:{channel_identity.get('channelId')}:{msg.get('replyToId')}"
            )

    source_id = message_id
    if channel_identity:
        source_id = f"{org}:{channel_identity.get('teamId')}:{channel_identity.get('channelId')}:{message_id}"

    team_id = (channel_identity or {}).get("teamId", "")
    channel_id = (channel_identity or {}).get("channelId", "")

    # Per LOR-14, construct a curated raw payload.
    # reply_to_id uses the composite source_id of the root message so that
    # group_by_thread() can match replies to their root using a single key
    # comparison (root source_id == reply raw["reply_to_id"]).
    reply_to_id: str | None = None
    if msg.get("replyToId") and team_id and channel_id:
        reply_to_id = f"{org}:{team_id}:{channel_id}:{msg['replyToId']}"

    # scope_id is stamped here so the grouping stage can hoist it onto the
    # thread dict when scope_in_raw=True (LOR-66 Teams ACL propagation).
    raw_payload = {
        "team_id": team_id,
        "channel_id": channel_id,
        "scope_id": scopes.ms365_channel_scope(channel_id) if channel_id else None,
        "reply_to_id": reply_to_id,
        "message_type": msg.get("messageType"),
    }

    return KnowledgeEvent(
        id=make_id("ms_teams", source_id),
        source="ms_teams",
        source_id=source_id,
        event_type=EventType.TEAM_MESSAGE,
        timestamp=msg.get("createdDateTime"),
        title=title,
        body=body_clean[:4000],
        primary_actor=author.id if author else None,
        actors=actors,
        url=msg.get("webUrl"),
        org=org,
        project=f"{team_name}#{channel_name}",
        tags=["teams", f"team:{team_id}"],
        parent_id=parent_id,
        raw=raw_payload,
    )
