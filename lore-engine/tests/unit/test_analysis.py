"""
tests/unit/test_analysis.py
───────────────────────────
domain/analysis.py — keyword fallback, JSON parsing, and the LLM path with a
fake ChatProvider. No network.
"""

from core.types import QueryAnalysis
from domain.analysis import analyze_query, keyword_analysis, parse_analysis


# ── Fake ChatProvider ─────────────────────────────────────────────────

class FakeChat:
    name = "fake"
    model = "fake"

    def __init__(self, reply: str = "", raises: bool = False):
        self._reply = reply
        self._raises = raises

    def chat(self, messages, **kw) -> str:
        if self._raises:
            raise RuntimeError("provider down")
        return self._reply

    def chat_stream(self, messages, **kw):
        yield ("content", self._reply)


NOW = "2026-06-12"


# ── Keyword fallback ──────────────────────────────────────────────────

def test_keyword_reasoning():
    assert keyword_analysis("Why did we drop the v2 webhook?").intent == "REASONING"


def test_keyword_person_lookup_and_name_extraction():
    a = keyword_analysis("Who approved the GST change, Priya Sharma?")
    assert a.intent in ("PERSON_LOOKUP", "DECISION_LOOKUP")  # both keywords present; first wins
    assert "Priya Sharma" in a.persons


def test_keyword_decision():
    assert keyword_analysis("What was decided about the AWS renewal?").intent == "DECISION_LOOKUP"


def test_keyword_global():
    assert keyword_analysis("What are the main themes this quarter?").intent == "GLOBAL"


def test_keyword_default_summary():
    assert keyword_analysis("anything at all").intent == "SUMMARY"


# ── JSON parsing ──────────────────────────────────────────────────────

def test_parse_plain_json():
    raw = '{"intent": "TASK_LOOKUP", "persons": ["Ravi"], "orgs": [], "topics": ["billing"], "time_range": null}'
    a = parse_analysis(raw)
    assert isinstance(a, QueryAnalysis)
    assert a.intent == "TASK_LOOKUP" and a.persons == ["Ravi"] and a.topics == ["billing"]


def test_parse_fenced_json_with_prose():
    raw = "Here you go:\n```json\n{\"intent\": \"REASONING\", \"persons\": [], \"orgs\": [], \"topics\": [], \"time_range\": {\"start\": \"2026-01-01\", \"end\": null}}\n```"
    a = parse_analysis(raw)
    assert a.intent == "REASONING"
    assert a.time_range and a.time_range.start == "2026-01-01"


def test_parse_garbage_returns_none():
    assert parse_analysis("not json at all") is None
    assert parse_analysis("") is None


def test_parse_invalid_intent_returns_none():
    # bad enum value → validation fails → None → caller falls back
    assert parse_analysis('{"intent": "BOGUS"}') is None


# ── analyze_query orchestration ───────────────────────────────────────

def test_analyze_uses_llm_when_valid():
    chat = FakeChat('{"intent": "EVENT_LOOKUP", "persons": [], "orgs": [], "topics": [], "time_range": null}')
    assert analyze_query(chat, "when did the kickoff happen?", now_iso=NOW).intent == "EVENT_LOOKUP"


def test_analyze_falls_back_on_llm_error():
    chat = FakeChat(raises=True)
    a = analyze_query(chat, "Why did we cancel?", now_iso=NOW)
    assert a.intent == "REASONING"  # keyword fallback kicked in


def test_analyze_falls_back_on_bad_json():
    chat = FakeChat("sorry I cannot help")
    a = analyze_query(chat, "What was decided?", now_iso=NOW)
    assert a.intent == "DECISION_LOOKUP"  # keyword fallback
