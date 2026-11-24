"""Normalizer for Zoho Mail messages and Zoho Cliq messages.

KnowledgeEvent field mapping (per the LOR-13 R&D doc):

    Mail  receivedTime -> timestamp   fromAddress -> sender (primary_actor)
          content      -> body        threadId    -> raw.thread_id (thread key)

    Cliq  time          -> timestamp  sender.id   -> sender (primary_actor)
          message       -> body       channel_id  -> raw.scope_id (channel ACL)
"""
from __future__ import annotations

import re
from typing import Any

from common import scopes
from common.hashing import make_id
from common.logger import get_logger
from common.schemas import Actor, EventType, KnowledgeEvent

log = get_logger("zoho.normalizer")

# ─── HTML Stripping ───────────────────────────────────────────────────────

_HTML_TAG_RE = re.compile("<[^>]+>")


def strip_html(text: str) -> str:
    """Regex-based HTML tag stripper (matches infra/outlook/normalizer.py)."""
    if not text:
        return ""
    return _HTML_TAG_RE.sub("", text)


# ─── Zoho Mail Normalizer ───────────────────────────────────────────────────

_ADDR_RE = re.compile(r'^\s*"?([^"<]*?)"?\s*<?\s*([\w.+-]+@[\w-]+(?:\.[\w-]+)+)\s*>?\s*$')


def _actor_from_zoho_address(raw_address: str) -> Actor:
    """Parses a Zoho Mail address string ('Name <a@b.com>' or 'a@b.com') into an Actor."""
    raw_address = (raw_address or "").strip()
    name, email = "", ""
    m = _ADDR_RE.match(raw_address)
    if m:
        name, email = m.group(1).strip(), m.group(2).strip()
    elif "@" in raw_address:
        email = raw_address
    if not name and email:
        name = email.split("@")[0]
    return Actor(
        id=f"email:{email.lower()}",
        display_name=name,
        email=email.lower(),
        source="zoho_mail",
    )


def normalize_zoho_mail(msg: dict[str, Any], org: str, account_email: str) -> KnowledgeEvent:
    """Normalizes a single Zoho Mail message into a KnowledgeEvent."""
    subject = msg.get("subject") or "(no subject)"
    message_id = str(msg.get("messageId", ""))
    thread_id = str(msg.get("threadId", ""))

    sender_raw = msg.get("fromAddress", "")
    sender = _actor_from_zoho_address(sender_raw) if sender_raw else None

    to_raw = msg.get("toAddress", "") or ""
    cc_raw = msg.get("ccAddress", "") or ""
    recipients = [
        _actor_from_zoho_address(addr)
        for addr in (to_raw.split(",") + cc_raw.split(","))
        if addr.strip()
    ]

    body_clean = strip_html(msg.get("content", "") or "")

    all_actors = {sender.id: sender} if sender else {}
    for r in recipients:
        if r.id not in all_actors:
            all_actors[r.id] = r

    domain = account_email.split("@")[-1] if "@" in account_email else ""
    tags = ["email"]
    if domain and sender and sender.email and domain not in (sender.email or ""):
        tags.append("external")
    else:
        tags.append("internal")

    raw_payload = {
        "thread_id": thread_id,
        "folder_id": msg.get("folderId"),
        "has_attachment": msg.get("hasAttachment"),
    }

    # receivedTime is epoch milliseconds as a string; convert to ISO 8601.
    timestamp = msg.get("receivedTime")
    if timestamp:
        try:
            from datetime import datetime, timezone

            timestamp = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass

    return KnowledgeEvent(
        id=make_id("zoho_mail", message_id),
        source="zoho_mail",
        source_id=message_id,
        event_type=EventType.EMAIL_MESSAGE,
        timestamp=timestamp,
        title=subject,
        body=body_clean[:4000],
        primary_actor=sender.id if sender else None,
        actors=[a.to_dict() for a in all_actors.values()],
        url=f"https://mail.zoho.com/zm/#mail/folder/inbox/message/{message_id}",
        org=org,
        project=subject,
        tags=tags,
        parent_id=make_id("zoho_mail.thread", thread_id) if thread_id else None,
        raw=raw_payload,
    )


# ─── Zoho Cliq Message Normalizer ───────────────────────────────────────────

def _actor_from_cliq_sender(sender: dict[str, Any] | None) -> Actor | None:
    """Creates an Actor from a Cliq message's `sender` object."""
    if not sender:
        return None
    user_id = str(sender.get("id", ""))
    display_name = sender.get("name") or "Unknown"
    email = sender.get("email")
    return Actor(
        id=f"zoho:{user_id}",
        display_name=display_name,
        email=email,
        source="zoho_cliq",
        is_bot=bool(sender.get("type") == "bot"),
    )


def normalize_cliq_message(msg: dict[str, Any], channel_name: str, org: str) -> KnowledgeEvent:
    """Normalizes a single Cliq channel message into a KnowledgeEvent."""
    message_id = str(msg.get("id") or msg.get("msg_id") or "")
    channel_id = str(msg.get("channel_id", ""))

    body_clean = strip_html(msg.get("message") or msg.get("content", "") or "")
    title = body_clean[:100]

    author = _actor_from_cliq_sender(msg.get("sender"))
    actors = [author.to_dict()] if author else []

    # Threaded replies carry the root message id in `thread_id` / `reply_to`.
    thread_id = str(msg.get("thread_id") or msg.get("reply_to") or "")
    parent_id = make_id("zoho_cliq", f"{channel_id}:{thread_id}") if thread_id else None

    source_id = f"{org}:{channel_id}:{message_id}"

    # scope_id is stamped here so the grouping stage can hoist it onto the
    # thread dict when scope_in_raw=True (mirrors MS Teams ACL propagation).
    raw_payload = {
        "channel_id": channel_id,
        "chat_id": channel_id,
        "scope_id": scopes.zoho_cliq_channel(channel_id) if channel_id else None,
        "thread_id": thread_id or None,
    }

    timestamp = msg.get("time")
    if timestamp:
        try:
            from datetime import datetime, timezone

            timestamp = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            pass

    return KnowledgeEvent(
        id=make_id("zoho_cliq", source_id),
        source="zoho_cliq",
        source_id=source_id,
        event_type=EventType.ZOHO_CLIQ_MESSAGE,
        timestamp=timestamp,
        title=title,
        body=body_clean[:4000],
        primary_actor=author.id if author else None,
        actors=actors,
        url=msg.get("message_url"),
        org=org,
        project=channel_name,
        tags=["cliq", f"channel:{channel_id}"],
        parent_id=parent_id,
        raw=raw_payload,
    )
