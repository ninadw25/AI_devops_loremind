"""
W1: tests for Slack channel + time-window sessionization
(pipeline/extraction/grouping.py:_sessionize_channel + group_by_thread dispatch).
"""

import json

from pipeline.extraction.grouping import _sessionize_channel, group_by_thread

CH = "C1"
GAP = 60 * 60  # 60 min


def _ev(ts, text="msg", thread_ts=None, channel="C1", title=None):
    """Build a minimal Slack KnowledgeEvent dict as the filtered file holds it."""
    raw = {"ts": str(ts), "channel_id": channel,
           "slack_meta": {"channel_id": channel},
           "scope_id": f"slack:channel:{channel}",
           "source_ref": f"{channel}:{thread_ts or ts}",
           "source_url": f"https://x.slack.com/archives/{channel}/p{ts}"}
    if thread_ts:
        raw["thread_ts"] = str(thread_ts)
    return {"id": f"{channel}:{ts}", "source": "slack", "source_id": f"{channel}:{ts}",
            "timestamp": f"2026-06-01T00:00:{int(ts) % 60:02d}+00:00",
            "title": title or text, "body": text, "raw": raw}


def test_messages_within_gap_form_one_session():
    base = 1_000_000
    evs = [_ev(base), _ev(base + 60), _ev(base + 120)]  # 1 min apart
    sessions = _sessionize_channel(CH, evs, GAP, 40)
    assert len(sessions) == 1
    assert len(sessions[0]["emails"]) == 3
    assert sessions[0]["thread_id"] == f"slack:session:{CH}:{base}"


def test_idle_gap_splits_sessions():
    base = 1_000_000
    evs = [_ev(base), _ev(base + 120),               # session A
           _ev(base + 2 * 60 * 60)]                  # 2h later → session B
    sessions = _sessionize_channel(CH, evs, GAP, 40)
    assert len(sessions) == 2
    assert [len(s["emails"]) for s in sessions] == [2, 1]


def test_reply_tree_folds_into_root_session_even_if_late():
    base = 1_000_000
    # root at base; a reply 3h later (past the gap) must still join the root's session
    root = _ev(base, "root")
    late_reply = _ev(base + 3 * 60 * 60, "late reply", thread_ts=base)
    sessions = _sessionize_channel(CH, [root, late_reply], GAP, 40)
    assert len(sessions) == 1
    assert len(sessions[0]["emails"]) == 2


def test_max_messages_cap_splits():
    base = 1_000_000
    evs = [_ev(base + i) for i in range(0, 90, 1)]  # 90 roots, 1s apart (no gap)
    sessions = _sessionize_channel(CH, evs, GAP, 40)
    assert len(sessions) == 3          # 40 + 40 + 10
    assert [len(s["emails"]) for s in sessions] == [40, 40, 10]


def test_orphan_reply_acts_as_root():
    base = 1_000_000
    # reply whose parent root is NOT in the window → becomes its own session
    orphan = _ev(base, "orphan reply", thread_ts=base - 9999)
    sessions = _sessionize_channel(CH, [orphan], GAP, 40)
    assert len(sessions) == 1
    assert sessions[0]["emails"][0]["body"] == "orphan reply"


def test_session_id_is_deterministic():
    base = 1_000_000
    evs = [_ev(base), _ev(base + 30)]
    a = _sessionize_channel(CH, evs, GAP, 40)
    b = _sessionize_channel(CH, list(reversed(evs)), GAP, 40)
    assert a[0]["thread_id"] == b[0]["thread_id"]  # order-independent


def test_scope_and_citation_hoisted():
    s = _sessionize_channel(CH, [_ev(1_000_000)], GAP, 40)[0]
    assert s["scope_id"] == f"slack:channel:{CH}"
    assert s["source_ref"] and s["source_url"]


def test_group_by_thread_dispatches_slack_to_sessions(tmp_path):
    base = 1_000_000
    evs = [_ev(base), _ev(base + 60),                 # session A (2 msgs)
           _ev(base + 5 * 60 * 60),                   # session B (1 msg, 5h later)
           _ev(base + 5 * 60 * 60 + 30, thread_ts=base + 5 * 60 * 60)]  # reply in B
    f = tmp_path / "techtest_slack_x.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in evs), encoding="utf-8")
    threads = group_by_thread(f)
    assert len(threads) == 2
    assert all(tid.startswith("slack:session:") for tid in threads)
    sizes = sorted(len(t["emails"]) for t in threads.values())
    assert sizes == [2, 2]  # A=2, B=root+reply=2
