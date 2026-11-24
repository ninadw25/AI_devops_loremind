"""
pipeline/jira/prose_prompts.py
───────────────────────────────
LLM system prompt and user-prompt builder for the Jira prose pass.

The Jira prose pass is the SCALPEL stage: it reads only the genuinely
unstructured part of a Jira issue (description + comments) and extracts
knowledge the structured fields do NOT capture — decisions reached in
discussion, rationale, blockers raised, concrete named facts.

Key design choices (from JIRA_EXTRACTION_AND_DEDUP.md §A4):
  • Anti-duplication is the CENTRAL instruction (not a footnote).
  • Empty output is explicitly endorsed — many issues have no prose worth extracting.
  • No Task node (the Issue IS the task).
  • Structured context block is injected as KNOWN FACTS so the model
    knows what is already in the graph.
"""

from __future__ import annotations

# Minimum characters of stripped prose content to bother calling the LLM.
# Issues with < PROSE_MIN_CHARS of prose are fully covered by the deterministic
# skeleton — skip the LLM to preserve the 40 RPM NVIDIA budget.
PROSE_MIN_CHARS = 80

JIRA_SYSTEM_PROMPT = """\
You are a Jira knowledge extractor. You receive two inputs:
  1. KNOWN STRUCTURED FACTS — the issue's key/type/status/reporter/assignee/links,
     which are ALREADY in the knowledge graph as structured edges.
  2. PROSE — the issue description and comments.

Your ONLY job is to extract knowledge that the PROSE ADDS beyond the structured facts:

  • DECISIONS reached in discussion — an approval, choice, agreement, rejection,
    or change of direction stated in the description or a comment.
  • RATIONALE — the reason or justification behind a decision (BECAUSE → Fact).
  • BLOCKERS / RISKS raised in discussion that are NOT an existing issue-link.
  • CONCRETE FACTS the prose states — deadlines, dates, numbers, named quantities,
    external systems — when they carry institutional knowledge.

DO NOT extract:
  • The assignee as a task — ASSIGNED_TO is already a field edge.
  • The status, reporter, priority, or issue links — already field edges.
  • A restatement of the issue summary with no added information.
  • Pleasantries, "+1", "thanks", status pings, or bot/automation comments.
  • Anything that merely echoes a structured fact in prose form.

NODE TYPES you may emit: Decision, Fact, Topic, Person, Issue (for cross-issue
mentions only — do NOT re-emit the current issue itself).

PREDICATES:
  DECIDED      Person → Decision     (someone decided something)
  BECAUSE      Decision → Fact       (rationale for a decision)
  REGARDING    Decision/Issue → Topic  (subject area)
  MENTIONS     Decision/Fact → Person|Issue  (cross-reference)

Attribute DECIDED / STATED knowledge to the Person the PROSE names as deciding
or stating it — NOT to the issue reporter by default.

Every triple needs an evidence_sentence: an EXACT short quote from the prose
(≥ 10 characters).

If the prose adds NOTHING beyond the structured facts, return an empty triples
list. This is common and correct — do NOT invent knowledge to fill the output.

Return ONLY valid JSON:
{
  "triples": [
    {
      "subject":  {"type": "Person|Decision|...", "name": "..."},
      "predicate": "DECIDED|BECAUSE|REGARDING|MENTIONS",
      "object":   {"type": "Decision|Fact|Topic|...", "description": "..."},
      "evidence_sentence": "exact quote"
    }
  ],
  "thread_summary": "One sentence summarising the prose content (empty string if no prose).",
  "department_tags": ["engineering|hr|operations|..."],
  "sensitivity": "internal|confidential|public"
}
"""


def build_jira_thread_prompt(prose_body: str, structured_ctx: str) -> str:
    """Build the user message for the Jira prose LLM call.

    Args:
        prose_body:     Truncated description + comments from truncate_body().
        structured_ctx: One-line summary of known structured facts (type, status,
                        reporter, assignee, links) from build_structured_context().
    """
    return (
        f"KNOWN STRUCTURED FACTS (already in graph — do NOT re-extract):\n"
        f"{structured_ctx}\n\n"
        f"PROSE (description + comments — extract ONLY what this ADDS):\n"
        f"{prose_body}"
    )


def build_structured_context(thread: dict, events: list[dict]) -> str:
    """Build a compact one-paragraph summary of known structured facts.

    This is given to the LLM so it doesn't re-extract what the deterministic
    builder already captured.
    """
    root = None
    thread_id = thread.get("thread_id", "")
    for ev in events:
        if ev.get("source_id") == thread_id:
            root = ev
            break
    if root is None and events:
        root = events[0]
    if root is None:
        return "(no structured facts)"

    raw = root.get("raw") or {}
    fields = raw.get("fields") or {}

    parts: list[str] = []

    issue_key = root.get("source_id") or raw.get("key") or thread_id
    issue_type = (fields.get("issuetype") or {}).get("name", "?")
    status = (fields.get("status") or {}).get("name", "?")
    parts.append(f"Issue: {issue_key}  ·  Type: {issue_type}  ·  Status: {status}")

    reporter = fields.get("reporter") or {}
    assignee = fields.get("assignee") or {}
    if reporter.get("displayName"):
        parts.append(f"Reporter: {reporter['displayName']}")
    if assignee.get("displayName"):
        parts.append(f"Assignee: {assignee['displayName']}")

    project = root.get("project") or (fields.get("project") or {}).get("key", "")
    if project:
        parts.append(f"Project: {project}")

    for epic_key in (root.get("related_ids") or []):
        if epic_key and epic_key != issue_key:
            parts.append(f"Epic: {epic_key}")
            break

    parent_key = root.get("parent_id")
    if parent_key:
        parts.append(f"Parent: {parent_key}")

    issuelinks = fields.get("issuelinks") or []
    link_strs: list[str] = []
    for link in issuelinks[:3]:
        ltype = (link.get("type") or {}).get("outward") or (link.get("type") or {}).get("name") or ""
        for direction in ("outwardIssue", "inwardIssue"):
            linked = (link.get(direction) or {}).get("key")
            if linked:
                link_strs.append(f"{ltype} {linked}")
    if link_strs:
        parts.append("Links: " + ", ".join(link_strs))

    return "  ".join(parts)


def extract_prose_body(thread: dict) -> str:
    """Extract the prose body from the thread dict.

    Returns the `body` field of the root issue event, which the normalizer
    already assembled via truncate_body(description, comments).
    """
    thread_id = thread.get("thread_id", "")
    for ev in (thread.get("emails") or []):
        if ev.get("source_id") == thread_id:
            return ev.get("body") or ""
    # Fallback: first event body
    events = thread.get("emails") or []
    return events[0].get("body", "") if events else ""
