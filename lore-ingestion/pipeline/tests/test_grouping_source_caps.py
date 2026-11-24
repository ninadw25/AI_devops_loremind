"""Lock the per-source thread-keying + ACL-hoist behaviour that the
SOURCE_CAPS table drives in group_by_thread (Slack vs Gmail vs Jira)."""

import json

from pipeline.extraction.grouping import group_by_thread
from common.sources import caps_for


def _write(tmp_path, events):
    p = tmp_path / "filtered.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return str(p)


def test_slack_sessionizes_reply_into_root_session_and_hoists_scope(tmp_path):
    # W1: Slack groups by channel+time-window session, not thread_ts. A root plus
    # its reply form ONE session keyed slack:session:{chan}:{first_root_ts}; ACL
    # scope + citation hoist from the first event.
    events = [
        {"id": "s1", "source": "slack", "title": "hi",
         "timestamp": "2026-01-01T00:00:00+00:00",
         "raw": {"ts": "111.2", "thread_ts": "111.2", "channel_id": "C1",
                 "slack_meta": {"channel_id": "C1"}, "scope_id": "slack:channel:C1",
                 "source_ref": "C1:111.2", "source_url": "https://x.slack.com/a"}},
        {"id": "s2", "source": "slack", "title": "re",
         "timestamp": "2026-01-01T00:00:30+00:00",
         "raw": {"ts": "131.2", "thread_ts": "111.2", "channel_id": "C1",
                 "slack_meta": {"channel_id": "C1"}}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"slack:session:C1:111.2"}
    s = th["slack:session:C1:111.2"]
    assert s["email_count"] == 2
    assert s["scope_id"] == "slack:channel:C1"
    assert s["source_ref"] == "C1:111.2"


def test_slack_single_message_is_one_session(tmp_path):
    events = [{"id": "s3", "source": "slack", "title": "solo",
               "timestamp": "2026-01-01T00:00:00+00:00",
               "raw": {"ts": "999.9", "channel_id": "C2",
                       "slack_meta": {"channel_id": "C2"},
                       "scope_id": "slack:channel:C2", "source_ref": "C2:999.9",
                       "source_url": "https://x/y"}}]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"slack:session:C2:999.9"}


def test_gmail_threads_by_thread_id_no_scope(tmp_path):
    events = [{"id": "g1", "source": "gmail", "title": "mail", "timestamp": "2026-01-01",
               "raw": {"thread_id": "T-gmail"}}]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"T-gmail"}
    assert "scope_id" not in th["T-gmail"]


def test_gmail_thread_scope_ids_from_actors(tmp_path):
    """scope_ids is unioned from raw["scope_ids"] stamped at normalize time."""
    events = [
        {"id": "g2", "source": "gmail", "title": "hello", "timestamp": "2026-01-01",
         "raw": {"thread_id": "T-abc",
                 "scope_ids": ["person:alice@corp.com", "person:bob@corp.com"]}},
        {"id": "g3", "source": "gmail", "title": "reply", "timestamp": "2026-01-02",
         "raw": {"thread_id": "T-abc",
                 "scope_ids": ["person:bob@corp.com", "person:carol@corp.com"]}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert th["T-abc"]["scope_ids"] == [
        "person:alice@corp.com",
        "person:bob@corp.com",
        "person:carol@corp.com",
    ]


def test_gmail_thread_no_scope_ids_when_no_actor_emails(tmp_path):
    """Threads with actors missing email fields produce no scope_ids (fail-closed)."""
    events = [
        {"id": "g4", "source": "gmail", "title": "x", "timestamp": "2026-01-01",
         "raw": {"thread_id": "T-nomail"},
         "actors": [{"email": ""}]},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert "scope_ids" not in th["T-nomail"]


def test_jira_threads_by_thread_id_and_hoists_top_level_scope(tmp_path):
    events = [
        {"id": "j1", "source": "jira", "title": "issue", "timestamp": "2026-01-01",
         "parent_id": None, "scope_id": "jira:project:ENG",
         "extra_scope_ids": ["jira:seclevel:1"], "source_ref": "ENG-1",
         "raw": {"thread_id": "ENG-1"}},
        {"id": "j2", "source": "jira", "title": "comment", "timestamp": "2026-01-02",
         "parent_id": "ENG-1", "raw": {}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"ENG-1"}
    assert th["ENG-1"]["email_count"] == 2
    assert th["ENG-1"]["scope_id"] == "jira:project:ENG"
    assert th["ENG-1"]["extra_scope_ids"] == ["jira:seclevel:1"]


# ── outlook_mail ──────────────────────────────────────────────────────────────

def test_outlook_mail_caps():
    """caps_for("outlook_mail") returns the correct SourceCaps."""
    caps = caps_for("outlook_mail")
    assert caps.thread_key_raw_fields == ("conversation_id",)
    assert caps.scope_in_raw is False
    assert caps.structured is False
    assert caps.group_strategy == "thread"


def test_outlook_mail_threads_by_conversation_id(tmp_path):
    """Two messages with the same conversation_id collapse into one thread."""
    events = [
        {"id": "om1", "source": "outlook_mail", "title": "Budget Q3",
         "timestamp": "2026-06-01T10:00:00Z",
         "raw": {"conversation_id": "CONV-001",
                 "internet_message_id": "<a@corp.com>"}},
        {"id": "om2", "source": "outlook_mail", "title": "Re: Budget Q3",
         "timestamp": "2026-06-01T11:00:00Z",
         "raw": {"conversation_id": "CONV-001",
                 "internet_message_id": "<b@corp.com>"}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"CONV-001"}
    assert th["CONV-001"]["email_count"] == 2


def test_outlook_mail_different_conversations_are_separate_threads(tmp_path):
    """Messages from different conversations produce separate thread entries."""
    events = [
        {"id": "om3", "source": "outlook_mail", "title": "Thread A",
         "timestamp": "2026-06-01T09:00:00Z",
         "raw": {"conversation_id": "CONV-A"}},
        {"id": "om4", "source": "outlook_mail", "title": "Thread B",
         "timestamp": "2026-06-01T09:30:00Z",
         "raw": {"conversation_id": "CONV-B"}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {"CONV-A", "CONV-B"}
    assert th["CONV-A"]["email_count"] == 1
    assert th["CONV-B"]["email_count"] == 1


def test_outlook_mail_no_scope_id_on_thread(tmp_path):
    """Outlook Mail threads carry no scope_id from grouping — stamped later by
    _populate_gmail_scopes() using participant person_ids (scope_in_raw=False)."""
    events = [
        {"id": "om5", "source": "outlook_mail", "title": "No scope yet",
         "timestamp": "2026-06-01T10:00:00Z",
         "raw": {"conversation_id": "CONV-002"}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert "scope_id" not in th["CONV-002"]


# ── ms_teams ──────────────────────────────────────────────────────────────────

def test_ms_teams_caps():
    """caps_for("ms_teams") returns the correct SourceCaps."""
    caps = caps_for("ms_teams")
    assert caps.thread_key_raw_fields == ("reply_to_id",)
    assert caps.scope_in_raw is True
    assert caps.structured is False
    assert caps.group_strategy == "thread"


def test_ms_teams_root_and_reply_collapse_into_one_thread(tmp_path):
    """Root message + reply collapse into a single thread.

    Root:  raw["reply_to_id"] = None  → key falls through to source_id
           source_id = "corp:T-1:C-1:MSG-001"
    Reply: raw["reply_to_id"] = "corp:T-1:C-1:MSG-001"  (Phase 0 composite fix)
           → key = "corp:T-1:C-1:MSG-001"  → matches root's thread
    """
    root_source_id = "corp:T-1:C-1:MSG-001"
    events = [
        {"id": "tm1", "source": "ms_teams", "title": "Deploy tonight?",
         "source_id": root_source_id,
         "timestamp": "2026-06-01T10:00:00Z",
         "raw": {
             "team_id": "T-1", "channel_id": "C-1",
             "scope_id": "ms365:channel:C-1",
             "reply_to_id": None,
             "message_type": "message",
         }},
        {"id": "tm2", "source": "ms_teams", "title": "Yes, confirmed.",
         "source_id": "corp:T-1:C-1:MSG-002",
         "timestamp": "2026-06-01T10:05:00Z",
         "raw": {
             "team_id": "T-1", "channel_id": "C-1",
             "scope_id": "ms365:channel:C-1",
             "reply_to_id": root_source_id,   # composite — matches root source_id
             "message_type": "message",
         }},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {root_source_id}
    assert th[root_source_id]["email_count"] == 2


def test_ms_teams_scope_id_hoisted_from_raw(tmp_path):
    """scope_id is hoisted from raw onto the thread dict when scope_in_raw=True."""
    root_source_id = "corp:T-2:C-2:MSG-010"
    events = [
        {"id": "tm3", "source": "ms_teams", "title": "Stand-up",
         "source_id": root_source_id,
         "timestamp": "2026-06-01T09:00:00Z",
         "raw": {
             "team_id": "T-2", "channel_id": "C-2",
             "scope_id": "ms365:channel:C-2",
             "reply_to_id": None,
             "message_type": "message",
         }},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert th[root_source_id]["scope_id"] == "ms365:channel:C-2"


def test_ms_teams_reply_arriving_before_root_still_groups_correctly(tmp_path):
    """Out-of-order delivery: reply arrives before root in the JSONL.

    Reply:  raw["reply_to_id"] = "corp:T-3:C-3:MSG-100"
            → thread created under key "corp:T-3:C-3:MSG-100"
            → scope_id hoisted from reply's raw (same channel, same scope)
    Root:   raw["reply_to_id"] = None
            → falls through to source_id = "corp:T-3:C-3:MSG-100"
            → thread already exists → root appended
    Result: one thread with 2 messages, correct scope_id.
    """
    root_source_id = "corp:T-3:C-3:MSG-100"
    events = [
        # reply first
        {"id": "tm5", "source": "ms_teams", "title": "Sounds good",
         "source_id": "corp:T-3:C-3:MSG-101",
         "timestamp": "2026-06-01T10:02:00Z",
         "raw": {
             "team_id": "T-3", "channel_id": "C-3",
             "scope_id": "ms365:channel:C-3",
             "reply_to_id": root_source_id,
             "message_type": "message",
         }},
        # root second
        {"id": "tm4", "source": "ms_teams", "title": "Anyone free?",
         "source_id": root_source_id,
         "timestamp": "2026-06-01T10:00:00Z",
         "raw": {
             "team_id": "T-3", "channel_id": "C-3",
             "scope_id": "ms365:channel:C-3",
             "reply_to_id": None,
             "message_type": "message",
         }},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {root_source_id}
    assert th[root_source_id]["email_count"] == 2
    assert th[root_source_id]["scope_id"] == "ms365:channel:C-3"


def test_ms_teams_two_separate_channels_produce_separate_threads(tmp_path):
    """Root messages from different channels produce independent threads."""
    sid_a = "corp:T-4:C-A:MSG-001"
    sid_b = "corp:T-4:C-B:MSG-001"
    events = [
        {"id": "tm6", "source": "ms_teams", "title": "Channel A msg",
         "source_id": sid_a, "timestamp": "2026-06-01T10:00:00Z",
         "raw": {"team_id": "T-4", "channel_id": "C-A",
                 "scope_id": "ms365:channel:C-A",
                 "reply_to_id": None, "message_type": "message"}},
        {"id": "tm7", "source": "ms_teams", "title": "Channel B msg",
         "source_id": sid_b, "timestamp": "2026-06-01T10:01:00Z",
         "raw": {"team_id": "T-4", "channel_id": "C-B",
                 "scope_id": "ms365:channel:C-B",
                 "reply_to_id": None, "message_type": "message"}},
    ]
    th = group_by_thread(_write(tmp_path, events))
    assert set(th) == {sid_a, sid_b}
    assert th[sid_a]["scope_id"] == "ms365:channel:C-A"
    assert th[sid_b]["scope_id"] == "ms365:channel:C-B"
