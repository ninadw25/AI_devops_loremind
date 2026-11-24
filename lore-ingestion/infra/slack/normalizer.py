"""
infra/slack/normalizer.py
Converted from upstream slack-connector; imports adapted to repo `common` modules.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from common import scopes
from common.config import get_settings
from common.schemas import Actor, KnowledgeEvent, EventType
from common.hashing import make_slack_id

# NOISE_SUBTYPES and regex constants (same as upstream)
NOISE_SUBTYPES: frozenset[str] = frozenset({
    "channel_join",    "channel_leave",   "channel_topic",   "channel_purpose",
    "channel_name",    "channel_archive",  "channel_unarchive",
    "group_join",      "group_leave",      "group_topic",     "group_purpose",
    "bot_message",     "tombstone",        "thread_broadcast",
})

_EMOJI_RE = re.compile(r"^[\U0001F300-\U0001FFFF\U00002600-\U000027BF☀-➿\s]+$")
_URL_ONLY_RE = re.compile(r"^https?://\S+$")
_MENTION_RE = re.compile(r"<@(U[A-Z0-9]+)>")


def is_noise(msg: dict, allowed_bot_ids: Optional[set[str]] = None) -> tuple[bool, str]:
    subtype = msg.get("subtype", "")
    if subtype in NOISE_SUBTYPES and subtype != "bot_message":
        return True, f"noise subtype: {subtype}"

    bot_identity = {
        str(value).strip()
        for value in (
            msg.get("bot_id"),
            msg.get("user"),
            msg.get("username"),
            msg.get("app_id"),
        )
        if value
    }

    if subtype == "bot_message" or msg.get("bot_id"):
        if allowed_bot_ids and bot_identity.intersection(allowed_bot_ids):
            return False, ""
        return True, "bot_id present"

    text = msg.get("text", "").strip()
    if len(text) < 3:
        return True, f"text too short ({len(text)} chars)"
    if _EMOJI_RE.match(text):
        return True, "emoji-only text"
    if _URL_ONLY_RE.match(text):
        return True, "url-only text"
    if not msg.get("user") and not msg.get("bot_id"):
        return True, "no user field"
    return False, ""


def compose_message_body(msg: dict) -> str:
    parts: list[str] = []
    text = (msg.get("text") or "").strip()
    if text:
        parts.append(text)

    for attachment in msg.get("attachments", []) or []:
        for key in ("text", "fallback", "title", "pretext"):
            value = attachment.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())

    for file_obj in msg.get("files", []) or []:
        file_parts: list[str] = []
        for key in ("content", "plain_text", "preview", "initial_comment", "text"):
            value = file_obj.get(key)
            if isinstance(value, str) and value.strip():
                file_parts.append(value.strip())
        if file_parts:
            parts.append("\n".join(file_parts))
            continue
        title = file_obj.get("title") or file_obj.get("name")
        if title:
            parts.append(f"[file] {title}")

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in seen:
            deduped.append(part)
            seen.add(part)
    return "\n\n".join(deduped)


def channel_weight(channel: dict) -> str:
    if channel.get("is_im"):
        return "very_low"
    if channel.get("is_mpim"):
        return "low"
    if channel.get("is_group") or channel.get("is_private"):
        return "medium"
    return "high"


def channel_type_tag(channel: dict) -> str:
    if channel.get("is_im"):
        return "dm"
    if channel.get("is_mpim"):
        return "group_dm"
    if channel.get("is_group") or channel.get("is_private"):
        return "private"
    return "public"


def channel_sharing_tag(channel: dict) -> Optional[str]:
    if channel.get("is_shared") or channel.get("is_ext_shared") or channel.get("is_org_shared"):
        return "shared"
    return None


def determine_event_type(msg: dict, channel: dict) -> str:
    if channel.get("is_im"):
        return EventType.SLACK_DM
    if channel.get("is_mpim"):
        return EventType.SLACK_GDM
    if msg.get("subtype") in {"tombstone", "message_deleted"}:
        return EventType.SLACK_DELETED
    msg_ts = msg.get("ts", "")
    thread_ts = msg.get("thread_ts", "")
    if thread_ts and msg_ts != thread_ts:
        return EventType.SLACK_REPLY
    if msg.get("reply_count", 0) >= 3:
        return EventType.SLACK_DISCUSSION
    return EventType.SLACK_MESSAGE


def ts_to_iso(ts_str: str) -> str:
    return datetime.fromtimestamp(float(ts_str), tz=timezone.utc).isoformat()


def _normalize_subdomain(domain: str) -> str:
    """Reduce a configured domain to a bare workspace subdomain.

    Accepts a bare subdomain ("acme"), a full host ("acme.slack.com"), or a
    URL ("https://acme.slack.com/"). Returns "" for values that are not a real
    workspace subdomain (empty, "slack.com", "www") so the link builder can
    fall back instead of emitting the broken "slack.com.slack.com" host.
    """
    d = (domain or "").strip().lower().rstrip("/")
    d = d.removeprefix("https://").removeprefix("http://")
    if d.endswith(".slack.com"):
        d = d[: -len(".slack.com")]
    if d in ("", "slack.com", "www"):
        return ""
    return d


def _workspace_domain(channel: dict) -> str:
    env_domain = get_settings().slack_workspace_domain
    normalized = _normalize_subdomain(env_domain)
    if normalized:
        return normalized
    return _normalize_subdomain(str(channel.get("team_domain") or ""))


def _slack_deep_link(channel_id: str, ts_str: str, workspace_domain: str) -> str:
    sec, _, frac = str(ts_str).partition(".")
    frac = (frac or "")[:6].ljust(6, "0")
    packed_ts = f"{sec}{frac}"
    if workspace_domain:
        return f"https://{workspace_domain}.slack.com/archives/{channel_id}/p{packed_ts}"
    # Unknown workspace subdomain → workspace-agnostic deep link (Slack resolves
    # it in-app for a logged-in user). Avoids the malformed double-domain host.
    return f"https://app.slack.com/archives/{channel_id}/p{packed_ts}"


def extract_mentions(text: str) -> list[str]:
    return _MENTION_RE.findall(text)


# ── W2: humanize Slack mrkdwn so the extractor LLM reads names, not ids ──────
_RE_USER    = re.compile(r"<@(U[A-Z0-9]+)(?:\|([^>]+))?>")
_RE_CHANNEL = re.compile(r"<#(C[A-Z0-9]+)(?:\|([^>]+))?>")
_RE_SPECIAL = re.compile(r"<!(\w+)(?:\^[^|>]+)?(?:\|([^>]+))?>")
_RE_LINK    = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]+))?>")


def _user_display(uid: str, user_cache: Optional[dict[str, dict]]) -> str:
    """Resolve a Slack user id to a display name (mirrors build_actor's order)."""
    user = (user_cache or {}).get(uid)
    if not user:
        return ""
    profile = user.get("profile", {}) or {}
    return (profile.get("display_name") or profile.get("real_name")
            or user.get("real_name") or user.get("name") or "")


def humanize_slack_text(text: str, user_cache: Optional[dict[str, dict]] = None) -> str:
    """Rewrite Slack mrkdwn control sequences into human-readable text.

    `<@U…>` → @Name, `<#C…|name>` → #name, `<!here>` → @here,
    `<!subteam^S…|@team>` → @team, `<http://url|label>` → label.

    Pure and defensive: an unresolved id degrades to a readable fallback
    (`@U…`, `#C…`) and never raises. The raw, un-humanized text is preserved
    in the event's `raw` payload for provenance.
    """
    if not text:
        return text

    def _user(m: "re.Match") -> str:
        uid, label = m.group(1), m.group(2)
        name = _user_display(uid, user_cache) or (label or "").strip()
        return f"@{name}" if name else f"@{uid}"

    def _channel(m: "re.Match") -> str:
        cid, label = m.group(1), m.group(2)
        return f"#{label.strip()}" if label else f"#{cid}"

    def _special(m: "re.Match") -> str:
        kw, label = m.group(1), m.group(2)
        return label.strip() if label else f"@{kw}"  # @here/@channel/@everyone

    def _link(m: "re.Match") -> str:
        url, label = m.group(1), m.group(2)
        if url.startswith("mailto:"):
            return (label or url[len("mailto:"):]).strip()
        return label.strip() if label else url

    text = _RE_USER.sub(_user, text)
    text = _RE_CHANNEL.sub(_channel, text)
    text = _RE_SPECIAL.sub(_special, text)
    text = _RE_LINK.sub(_link, text)
    # Slack escapes only these three in message text; decode &amp; last.
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return text


DECISION_MARKERS: tuple[str, ...] = (
    "we decided", "decision:", "going with", "approved",
    "we're doing", "final call", "confirmed", "settled on", "agreed to",
    "decision confirmed",
)

TASK_MARKERS: tuple[str, ...] = (
    "can you", "please do", "assigned to", "by friday", "by eod",
    "by monday", "will handle", "owns the", "i'll handle", "cover my",
)


def classify_thread(parent_text: str, replies: list[dict]) -> str:
    combined = (parent_text + " " + " ".join(r.get("text", "") for r in replies)).lower()
    for marker in DECISION_MARKERS:
        if marker in combined:
            return "Decision"
    for marker in TASK_MARKERS:
        if marker in combined:
            return "Task"
    return "Fact"


def truncate_thread_for_llm(messages: list[dict]) -> str:
    if not messages:
        return ""
    parent = messages[0]
    replies = messages[1:]
    n = len(replies)
    if n == 0:
        return parent.get("text", "")[:3000]
    if n <= 10:
        parts = [parent.get("text", "")[:1500]]
        for r in replies:
            parts.append(f"[reply] {r.get('text', '')}")
        return "\n".join(parts)
    parts = [parent.get("text", "")[:1000]]
    for r in replies[:3]:
        parts.append(f"[reply] {r.get('text', '')}")
    middle = n - 6
    parts.append(f"[{middle} middle replies omitted]")
    for r in replies[-3:]:
        parts.append(f"[reply] {r.get('text', '')}")
    return "\n".join(parts)


def noise_category(reason: str) -> str:
    """Map a verbose is_noise() reason to a stable, greppable category key."""
    if reason.startswith("noise subtype"):
        return "noise_subtype"
    if reason.startswith("bot_id"):
        return "bot"
    if reason.startswith("text too short"):
        return "text_too_short"
    if reason.startswith("emoji-only"):
        return "emoji_only"
    if reason.startswith("url-only"):
        return "url_only"
    if reason.startswith("no user"):
        return "no_user"
    return "other"


def normalize_message(
    msg: dict,
    channel: dict,
    org: str,
    user_cache: Optional[dict[str, dict]] = None,
    allowed_bot_ids: Optional[set[str]] = None,
    noise_counter: Optional[dict[str, int]] = None,
) -> Optional[KnowledgeEvent]:
    noise, reason = is_noise(msg, allowed_bot_ids)
    if noise:
        if noise_counter is not None:
            category = noise_category(reason)
            noise_counter[category] = noise_counter.get(category, 0) + 1
        return None
    return build_knowledge_event(msg, channel, org, user_cache=user_cache)


def build_actor(user: dict, source: str = "slack") -> Actor:
    user_id = user["id"]
    profile = user.get("profile", {})
    display_name = (
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or user.get("name")
        or user_id
    )
    email = profile.get("email") or None
    return Actor(
        id=scopes.slack_user(user_id),
        display_name=display_name,
        email=email,
        source=source,
        is_bot=bool(user.get("is_bot", False)),
        is_deleted=bool(user.get("deleted", False)),
    )


def build_knowledge_event(msg: dict, channel: dict, org: str, user_cache: Optional[dict[str, dict]] = None) -> KnowledgeEvent:
    channel_id = channel["id"]
    msg_ts = msg["ts"]
    thread_ts = msg.get("thread_ts", "")
    subtype = msg.get("subtype", "")

    event_id = make_slack_id(org, channel_id, msg_ts)
    source_id = f"{channel_id}:{msg_ts}"

    parent_id: Optional[str] = None
    if thread_ts and msg_ts != thread_ts:
        parent_id = make_slack_id(org, channel_id, thread_ts)

    text = compose_message_body(msg)
    text = humanize_slack_text(text, user_cache)  # W2: ids → names before extraction
    title = text[:100] if text else ("[deleted message]" if subtype in {"tombstone", "message_deleted"} else "")

    actors: list[Actor] = []
    author_id = msg.get("user")
    bot_id = msg.get("bot_id")
    if author_id:
        if user_cache and author_id in user_cache:
            actors.append(build_actor(user_cache[author_id]))
        else:
            actors.append(Actor(id=scopes.slack_user(author_id), display_name=author_id, source="slack"))
    elif bot_id:
        actors.append(Actor(id=scopes.slack_user(bot_id), display_name=msg.get("username") or bot_id, source="slack", is_bot=True))

    parent_user_id = msg.get("parent_user_id")
    if parent_user_id and parent_user_id != author_id:
        actor_id = scopes.slack_user(parent_user_id)
        if not any(a.id == actor_id for a in actors):
            if user_cache and parent_user_id in user_cache:
                actors.append(build_actor(user_cache[parent_user_id]))
            else:
                actors.append(Actor(id=actor_id, display_name=parent_user_id, source="slack"))

    node_type = classify_thread(text, [])
    type_tag = channel_type_tag(channel)

    slack_meta = {
        "channel_weight": channel_weight(channel),
        "channel_id": channel_id,
        "reply_count": msg.get("reply_count", 0),
        "reaction_count": len(msg.get("reactions", [])),
        "is_edited": "edited" in msg,
        "is_deleted": subtype in {"tombstone", "message_deleted"},
        "subtype": subtype or None,
        "is_shared": bool(channel.get("is_shared") or channel.get("is_ext_shared") or channel.get("is_org_shared")),
    }

    shared_tag = channel_sharing_tag(channel)
    tags = [type_tag, node_type]
    if shared_tag and shared_tag not in tags:
        tags.append(shared_tag)

    ref_ts = thread_ts if thread_ts else msg_ts
    workspace_domain = _workspace_domain(channel)
    source_url = _slack_deep_link(channel_id, ref_ts, workspace_domain)

    raw_payload = {
        **msg,
        "slack_meta": slack_meta,
        "scope_id": scopes.slack_channel(channel_id),
        "source_ref": f"{channel_id}:{ref_ts}",
        "source_url": source_url,
    }

    return KnowledgeEvent(
        id=event_id,
        source="slack",
        source_id=source_id,
        event_type=determine_event_type(msg, channel),
        timestamp=ts_to_iso(msg_ts),
        title=title,
        body=text,
        primary_actor=(scopes.slack_user(author_id) if author_id else "slack:unknown"),
        actors=[a.to_dict() for a in actors],
        org=org,
        project=channel.get("name", ""),
        tags=tags,
        parent_id=parent_id,
        raw=raw_payload,
    )
