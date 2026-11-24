"""
domain/analysis.py
──────────────────
Stage 1 of the unstructured query pipeline: turn a question into a structured
QueryAnalysis (intent + entity surface forms + time range) with ONE small LLM
call. See docs/QUERY_PIPELINE_V2.md §3.

Two entry points:
  analyze_query(chat, question, *, now_iso)
      → QueryAnalysis. Calls the injected ChatProvider, parses + validates the
        JSON, and falls back to keyword_analysis() on any LLM error, malformed
        JSON, or schema violation. Always returns — never raises.

  keyword_analysis(question)
      → QueryAnalysis. Pure, IO-free keyword classifier ported from the
        research prototype (lore-graph-research/src/intent_analyzer.py). Used as
        the fallback above and by QueryService when the analysis call exceeds
        its deadline.

Threading / hard-timeout is intentionally NOT here: the deadline and the
embed-vs-analyze concurrency belong to the orchestration layer
(services/query_service.py), which runs analyze_query() in a worker with a
timeout and calls keyword_analysis() if it fires. This keeps domain/ free of
thread management and trivially unit-testable.

The GLOBAL intent (community-summary questions) is recognised but currently
unserved — QueryService degrades it to TOPIC_EXPLORATION and logs
intent_global_unserved so demand can be measured before building summaries.
"""

from __future__ import annotations

import json
import re

from core.logging import get_logger
from core.observability import observe
from core.types import ChatProvider, QueryAnalysis

log = get_logger(__name__)

# Capitalised multi-word names, e.g. "Priya Sharma". Best-effort person
# surface-form extraction for the keyword fallback only.
_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")

# Keyword → intent table (ordered: first match wins). Ported and extended from
# the research prototype with EVENT_LOOKUP and GLOBAL.
_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("REASONING",         ("why", "because", "reason", "rationale", "what led to")),
    ("PERSON_LOOKUP",     ("who ", "whom", "owner", "responsible", "in charge", "involved")),
    ("DECISION_LOOKUP",   ("decision", "decided", "approved", "chose", "outcome", "conclude")),
    ("TASK_LOOKUP",       ("task", "assigned", "action item", "to-do", "todo", "working on")),
    ("EVENT_LOOKUP",      ("meeting", "event", "call", "happened on", "when did")),
    ("GLOBAL",            ("main themes", "overall", "across the", "summarize everything",
                           "key topics", "in general", "this quarter", "company-wide")),
    ("TOPIC_EXPLORATION", ("tell me about", "what is", "what are", "explain", "overview of")),
]


# ── Fallback classifier (pure) ────────────────────────────────────────

def keyword_analysis(question: str) -> QueryAnalysis:
    """Keyword-based intent + best-effort person extraction. No IO."""
    q = question.lower()
    intent = "SUMMARY"
    for candidate, keywords in _INTENT_KEYWORDS:
        if any(kw in q for kw in keywords):
            intent = candidate
            break
    persons = list(dict.fromkeys(_NAME_RE.findall(question)))  # dedupe, keep order
    return QueryAnalysis(intent=intent, persons=persons, orgs=[], topics=[], time_range=None)


# ── JSON parsing ──────────────────────────────────────────────────────

def parse_analysis(raw: str) -> QueryAnalysis | None:
    """Extract and validate a QueryAnalysis from raw model output.

    Tolerates code fences and surrounding prose by extracting the first
    balanced {...} object. Returns None on any failure so the caller can
    fall back to keyword_analysis().
    """
    if not raw:
        return None
    text = raw.strip()
    # Strip ```json … ``` fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return QueryAnalysis.model_validate(data)
    except Exception as exc:  # JSONDecodeError or pydantic ValidationError
        log.warning("analysis_parse_failed", error=str(exc), raw=raw[:200])
        return None


# ── Prompt ────────────────────────────────────────────────────────────

_SYSTEM = (
    "You analyze a user's question about an organization's email and chat history "
    "(Gmail, Slack, Outlook, Teams). You do NOT answer it. Output ONLY a JSON object "
    "with exactly these keys:\n"
    '  "intent": one of PERSON_LOOKUP, TASK_LOOKUP, DECISION_LOOKUP, REASONING, '
    "EVENT_LOOKUP, TOPIC_EXPLORATION, GLOBAL, SUMMARY\n"
    '  "persons": array of person names mentioned, exactly as written (may be empty)\n'
    '  "orgs": array of company/organization names mentioned (may be empty)\n'
    '  "topics": array of subject/topic phrases the question is about (may be empty)\n'
    '  "time_range": {"start": ISO-8601 date or null, "end": ISO-8601 date or null} or null\n'
    "Pick REASONING for why/because questions, GLOBAL for broad 'main themes / overall' "
    "questions, SUMMARY when no other intent fits. Resolve relative dates "
    "('last week', 'this quarter') against the provided current date. Output JSON only."
)


def _user_msg(question: str, now_iso: str) -> str:
    return f"Current date: {now_iso}\nQuestion: {question}"


@observe(name="retrieval.analyze")
def analyze_query(chat: ChatProvider, question: str, *, now_iso: str) -> QueryAnalysis:
    """Run the analysis LLM call, parse it, fall back to keywords on any failure.

    Blocking and timeout-free by design (see module docstring). Always returns
    a QueryAnalysis.
    """
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_msg(question, now_iso)},
    ]
    try:
        raw = chat.chat(
            messages,
            max_tokens=300,
            temperature=0.0,
            enable_thinking=False,  # extraction task — reasoning wastes latency
        )
    except Exception as exc:
        log.warning("analysis_llm_failed", error=str(exc))
        return keyword_analysis(question)

    parsed = parse_analysis(raw)
    if parsed is None:
        return keyword_analysis(question)
    log.info("analysis_ok", intent=parsed.intent, persons=len(parsed.persons))
    return parsed
