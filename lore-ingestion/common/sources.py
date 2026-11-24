"""
common/sources.py
─────────────────
Per-source data-shape capabilities — the single place that records how each
connector shapes its normalized events, so processing stages can stay
source-agnostic instead of branching on `if source == "..."` inline.

Today the consumer is `pipeline/extraction/grouping.py` (thread keying + ACL
hoisting). As more shape differences appear, add a field here rather than a
new branch in the pipeline.

NOTE: this table is about DATA SHAPE only (where a field lives, what it's
called). Genuinely source-specific BEHAVIOUR — Jira's prompt hint, Jira's
dedicated noise filter — stays as named code paths where it reads clearer.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceCaps:
    # raw-level field names tried in order to derive the thread key, before
    # the common parent_id -> source_id fallback applied by the consumer.
    thread_key_raw_fields: tuple[str, ...]
    # True  → ACL scope_id and citation (source_ref/source_url) live in raw
    #         (Slack puts them there).
    # False → they arrive at the event top level, hoisted separately (Jira),
    #         or the source has no ACL scope (Gmail).
    scope_in_raw: bool
    # True  → source has machine-readable structured fields (Jira). The
    #         extraction stage runs a deterministic field→triple pass first,
    #         then a scalpel LLM prose pass (different system prompt, cost gate).
    # False → source is unstructured (Gmail, Slack); only the LLM path runs.
    structured: bool = False
    # How the extractor groups events into one LLM unit (W1):
    #   "thread"  → one reply-tree / email-thread per unit (Gmail, Jira).
    #   "session" → channel + time-window sessions (Slack): loose messages are
    #               bucketed by idle gap, reply-trees fold into their root's
    #               session. A standalone message is NOT its own LLM call.
    group_strategy: str = "thread"
    # Session-strategy knobs (ignored for "thread"):
    session_idle_gap_minutes: int = 60   # new session after this much channel silence
    session_max_messages: int = 40       # cap root messages/session (replies still fold in)


# Default for any source not explicitly listed: thread by raw.thread_id, no
# raw-level scope. Matches the original `else` branch in grouping.
DEFAULT_CAPS = SourceCaps(thread_key_raw_fields=("thread_id",), scope_in_raw=False)

SOURCE_CAPS: dict[str, SourceCaps] = {
    "gmail": SourceCaps(thread_key_raw_fields=("thread_id",), scope_in_raw=False),
    "slack": SourceCaps(thread_key_raw_fields=("thread_ts", "ts"), scope_in_raw=True,
                        group_strategy="session",
                        session_idle_gap_minutes=60, session_max_messages=40),
    "jira":  SourceCaps(thread_key_raw_fields=("thread_id",), scope_in_raw=False, structured=True),

    # ── Microsoft 365 ────────────────────────────────────────────────
    #
    # outlook_mail: mirrors the Gmail model.
    #   - thread_key_raw_fields: normalizer stores raw["conversation_id"]
    #     (snake_case) — the Graph conversationId is the thread key, same
    #     role as Gmail's thread_id.  All messages in a conversation share
    #     the same conversation_id so they collapse into one LLM unit.
    #   - scope_in_raw=False: Outlook Mail has no channel — scope is built
    #     from participant emails → person_ids by _populate_gmail_scopes()
    #     in extract.py, exactly as Gmail.  No ACL discovery step needed.
    "outlook_mail": SourceCaps(
        thread_key_raw_fields=("conversation_id",),
        scope_in_raw=False,
        structured=False,
        group_strategy="thread",
    ),

    # ms_teams: mirrors the Slack model for ACL, Gmail model for grouping.
    #   - thread_key_raw_fields: normalizer stores raw["reply_to_id"] as the
    #     composite source_id of the root message (fixed in LOR-66 Phase 0):
    #     "{org}:{teamId}:{channelId}:{rootMessageId}".
    #     Root messages have raw["reply_to_id"] = None → fall through to
    #     source_id, which is "{org}:{teamId}:{channelId}:{messageId}".
    #     This makes root source_id == reply reply_to_id → single key match.
    #   - scope_in_raw=True: normalizer stamps raw["scope_id"] =
    #     ms365:channel:{channelId} (fixed in LOR-66 Phase 0).
    #     group_by_thread() hoists this onto the thread dict on first creation.
    #   - group_strategy="thread": Teams replies are explicitly linked via
    #     replyToId — no time-window sessionization needed.  Each reply tree
    #     is a deterministic graph, not a heuristic session.
    "ms_teams": SourceCaps(
        thread_key_raw_fields=("reply_to_id",),
        scope_in_raw=True,
        structured=False,
        group_strategy="thread",
    ),

    # ── Zoho ─────────────────────────────────────────────────────────
    #
    # zoho_mail: mirrors the Gmail / Outlook Mail model.
    #   - thread_key_raw_fields: normalizer stores raw["thread_id"] (Zoho
    #     Mail's own threadId) — same role as Gmail's thread_id.
    #   - scope_in_raw=False: no channel; participant-based ACL like Gmail.
    "zoho_mail": SourceCaps(
        thread_key_raw_fields=("thread_id",),
        scope_in_raw=False,
        structured=False,
        group_strategy="thread",
    ),

    # zoho_cliq: mirrors the Slack model (channel ACL + session grouping).
    #   - thread_key_raw_fields: normalizer stores raw["thread_id"] when a
    #     Cliq message is a threaded reply, else raw["chat_id"] (channel id)
    #     so standalone messages still bucket by channel for sessionization.
    #   - scope_in_raw=True: normalizer stamps raw["scope_id"] =
    #     zoho:cliq:channel:{channel_id}.
    "zoho_cliq": SourceCaps(
        thread_key_raw_fields=("thread_id", "chat_id"),
        scope_in_raw=True,
        structured=False,
        group_strategy="session",
        session_idle_gap_minutes=60,
        session_max_messages=40,
    ),
}


def caps_for(source: str) -> SourceCaps:
    """Return the data-shape capabilities for a source (DEFAULT_CAPS if unknown)."""
    return SOURCE_CAPS.get(source, DEFAULT_CAPS)


__all__ = ["SourceCaps", "SOURCE_CAPS", "DEFAULT_CAPS", "caps_for"]
