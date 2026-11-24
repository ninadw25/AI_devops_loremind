"""
W3: lock per-source framing in build_thread_prompt — Slack gets the channel-session
hint + framing; Gmail/Jira framing unchanged.
"""

from pipeline.extraction.prompts import build_thread_prompt, SOURCE_HINTS


def _thread(source):
    return {
        "thread_id": "slack:session:C1:111.2" if source == "slack" else "T1",
        "scope_id": "slack:channel:C1" if source == "slack" else None,
        "source_ref": "C1:111.2" if source == "slack" else None,
        "emails": [{"source": source, "title": "ok lets switch to Razorpay",
                    "body": "ok lets switch billing to Razorpay",
                    "timestamp": "2026-06-01T09:00:00+00:00",
                    "primary_actor": "slack:U1", "actors": []}],
    }


def test_slack_uses_session_framing_and_hint():
    p = build_thread_prompt(_thread("slack"), {}, {})
    assert "time-window SESSION" in p           # SLACK_HINT present
    assert "CHANNEL SESSION:" in p
    assert "--- Message 1 of 1 ---" in p
    assert "Subject:" not in p                   # no email subject line
    assert "empty triples" in p                  # noise → empty instruction


def test_gmail_framing_unchanged():
    p = build_thread_prompt(_thread("gmail"), {}, {})
    assert "SESSION" not in p
    assert "Jira issue" not in p
    assert "THREAD ID:" in p and "EMAIL COUNT:" in p
    assert "--- Email 1 of 1 ---" in p


def test_jira_hint_intact():
    p = build_thread_prompt(_thread("jira"), {}, {})
    assert "Jira issue thread" in p
    assert "THREAD ID:" in p


def test_source_hints_map_has_no_inline_branches():
    # The only place a source attaches a hint.
    assert set(SOURCE_HINTS) == {"jira", "slack"}
