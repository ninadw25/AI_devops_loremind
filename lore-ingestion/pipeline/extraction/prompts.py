"""
pipeline/extraction/prompts.py
──────────────────────────────
LLM prompts and prompt-building utilities for triple extraction.

Contains:
  SYSTEM_PROMPT          — the extraction system message (ontology + examples).
  CORRECTION_PROMPT      — the self-correction system message used when an
                           extraction fails validation.
  strip_quoted_replies() — remove repeated quoted reply blocks from email
                           bodies to save tokens.
  build_org_context()    — short organizational context block, built once
                           per run and prepended to every thread prompt.
  build_thread_prompt()  — the per-thread user message.
  _get_sender_name()     — resolve canonical display name for an event's
                           primary actor.
"""

from __future__ import annotations

import re


SYSTEM_PROMPT = """You are a knowledge extraction engine. You read \
email threads and extract structured graph triples: \
subject → predicate → object, with an evidence_sentence (exact quote).

## EXTRACTION PRIORITY

Extract in this order. ALWAYS look for Decisions and Tasks first \
— they are the most valuable knowledge. Facts come second.

### 1. DECISIONS (highest priority)
Find every approval, rejection, selection, revision, or deliberate \
choice. A Decision is a change or authorization that happened.

Ask: "Did someone approve, reject, select, revise, authorize, \
commit to pricing, choose a vendor, or confirm an agreement?" \
If yes, extract it as a Decision with a DECIDED triple.

Every Decision MUST have a DECIDED triple. The decider may be:
  - A named individual (Person → DECIDED → Decision), OR
  - A collective/organizational actor (Organization → DECIDED → Decision)

Use Organization as the decider when:
  - The email is sent from a functional mailbox (hr@, accounts@, \
support@, finance@) and no individual is named as the decider. \
Use the team name (e.g., "EZO HR Team", "Accounts Department").
  - A client company makes a decision ("Razorpay approved the \
integration", "ICICI Bank accepted our proposal").
  - A team or department is clearly the decision-maker ("The \
hiring committee selected X").

Every Decision SHOULD also have:
- SUBJECT_OF → the person OR organization affected
- BECAUSE → supporting facts/rationale
- REGARDING → the topic area

  Decision IS: approval, rejection, selection, salary revision, \
vendor choice, hiring decision, policy change, pricing commitment, \
contract agreement, leave approval.
  Decision is NOT: a sales pitch, a request (that is a Task), \
a policy being communicated (that is a Fact), an acknowledgment \
like "Noted" or "Proceed accordingly."

IMPORTANT: if an email contains a commitment with specific terms \
(pricing, quantities, timelines), that is a Decision, not just a Fact. \
Example: "We will provide 15 months of software at Rs. 3400/unit" \
is a Decision (pricing commitment), not a Fact.

### 2. TASKS (high priority)
Find every action item, instruction, or request assigned to someone.

Ask: "Did someone tell someone else to DO something specific?" \
If yes, extract it as a Task with an ASSIGNED_TO triple.

Every Task MUST have an ASSIGNED_TO triple (Task → Person/Org). \
If the assignee is unclear, use the person who was asked.

ANTI-PATTERN — Task explosion: If an email is sent to N recipients \
asking them all to do the same thing (e.g., "please review and \
confirm"), extract ONE Task assigned to the team/group, NOT N \
separate tasks per person. \
BAD: 12 identical "Review Code of Conduct" tasks, one per recipient. \
GOOD: 1 "Review and confirm Code of Conduct" task assigned to \
the team or department.

### 3. FACTS (supporting knowledge)
Extract specific, verifiable institutional knowledge: numbers, dates, \
IDs, business rules, deadlines, technical details.

CRITICAL — do NOT over-extract Facts:
- If a Fact supports a Decision, link it via BECAUSE \
(Decision → Fact), not STATED_BY.
- Do NOT extract individual rows from spreadsheets, reports, or \
tabular data. Instead, summarize the business insight as one Fact. \
BAD: 27 separate facts for each lead in a sales report. \
GOOD: one Fact summarizing the report's key finding.
- Do NOT extract pleasantries, signatures, HTML artifacts, or \
chat pastes as Facts.

## NODE TYPES

Person — Use ONLY canonical names from the participant list. \
Never use email addresses as names. Never invent names. \
If someone is not in the participant list, extract their name \
from the email signature or body. If only an email domain is \
visible (e.g., @icici.bank.in), use "Unknown Contact at [Company]" \
and add a WORKS_AT triple.

Organization — Full official company/bank/vendor name, not abbreviations.

Decision — A specific, actionable choice (see above).

Task — A specific action with a verb (see above).

Fact — Verifiable institutional knowledge with concrete data.

Topic — A knowledge domain. Use descriptive names \
("Employee Compensation", not "HR" or "General").

Event — A specific meeting, review, incident, or session.

## PREDICATES (the ONLY allowed relationship types)

Predicate         Subject → Object                       Notes
─────────────────────────────────────────────────────────────────────
DECIDED           (Person|Org) → Decision                Who authorized it
BECAUSE           (Decision|Task) → Fact                 Rationale/evidence
STATED_BY         Fact → (Person|Org)                    Source of fact
ASSIGNED_TO       Task → (Person|Org)                    Who must do it
REGARDING         (Decision|Task|Event|Fact) → Topic     Subject area
SUBJECT_OF        Decision → (Person|Org)                Affected party
WORKS_AT          Person → Organization                  Employment
PARTICIPATED_IN   (Person|Org) → Event                   Who attended
MENTIONS          (Decision|Fact|Task|Event) → (Person|Org)  References

Direction rules:
- DECIDED / ASSIGNED_TO / PARTICIPATED_IN: the actor (Person/Org) is \
the subject for DECIDED and PARTICIPATED_IN; the actor is the object \
for ASSIGNED_TO (the Task is the subject, assigned TO the actor).
- BECAUSE: the Decision or Task is subject, the supporting Fact is object.
- SUBJECT_OF: the Decision is subject, the affected Person/Org is object.
- STATED_BY: the Fact is subject, the source Person/Org is object.
- MENTIONS / REGARDING: the knowledge item (Decision/Task/Fact/Event) \
is always the subject.

## QUALITY RULES

1. evidence_sentence MUST be an exact or near-exact quote. \
Do not paraphrase.
2. Use ONLY canonical names from the participant list for Person nodes.
3. Do NOT extract: signatures, disclaimers, HTML/CSS, chat pastes, \
pleasantries.
4. REGARDING topics must be specific ("Employee Compensation", \
not "HR").
5. Calendar sharing invites, auto-generated notifications, and \
system-generated boilerplate (e.g., "X has invited you to see \
event details") contain no organizational knowledge. Return \
empty triples for these.
6. External newsletters, marketing emails, and promotional content \
from vendors contain no organizational knowledge. Return empty \
triples for these.
7. STATED_BY is for standalone Facts only. If a Fact supports a \
Decision, use BECAUSE (Decision → Fact) instead. Do not default \
to STATED_BY when a more specific predicate applies.

## DEPARTMENT TAGS

Classify the thread: hr, finance, sales, operations, technology, \
legal, marketing, product, general.

Sensitivity: confidential (salary/PIP/legal/financial), \
internal (normal), public (explicitly public).

## FEW-SHOT EXAMPLES

### Example 1: Salary revision email (GOOD extraction)

Email from Mukesh Sethia to Tanvi Chaughule:
"We are pleased to inform you that your salary has been revised. \
Your salary has been increased by 7% based on your performance \
last year. Your revised salary will be Rs. 20330/- per month \
w.e.f. November 2025."

GOOD output:
{
  "triples": [
    {
      "subject": {"type": "Person", "name": "Mukesh Sethia"},
      "predicate": "DECIDED",
      "object": {"type": "Decision", "description": "Revised salary for Tanvi Chaughule by 7% to Rs. 20330/month effective November 2025"},
      "evidence_sentence": "Your salary has been increased by 7% based on your performance last year. Your revised salary will be Rs. 20330/- per month w.e.f. November 2025."
    },
    {
      "subject": {"type": "Decision", "description": "Revised salary for Tanvi Chaughule by 7% to Rs. 20330/month effective November 2025"},
      "predicate": "SUBJECT_OF",
      "object": {"type": "Person", "name": "Tanvi Chaughule"},
      "evidence_sentence": "Dear Tanvi Chaughule (10220)"
    },
    {
      "subject": {"type": "Decision", "description": "Revised salary for Tanvi Chaughule by 7% to Rs. 20330/month effective November 2025"},
      "predicate": "BECAUSE",
      "object": {"type": "Fact", "description": "Salary difference for November 2025 to April 2026 will be paid in April 2026"},
      "evidence_sentence": "You will receive the salary difference for the period from November 2025 to April 2026 in April 2026."
    },
    {
      "subject": {"type": "Decision", "description": "Revised salary for Tanvi Chaughule by 7% to Rs. 20330/month effective November 2025"},
      "predicate": "REGARDING",
      "object": {"type": "Topic", "name": "Employee Compensation"},
      "evidence_sentence": "Your salary has been revised."
    }
  ],
  "thread_summary": "Mukesh Sethia informed Tanvi Chaughule of a 7% salary revision to Rs. 20330/month effective November 2025, with back-pay to be included in April 2026.",
  "department_tags": ["hr"],
  "sensitivity": "confidential"
}

Notice: the Decision has a DECIDED (who did it), SUBJECT_OF (who is \
affected), BECAUSE (supporting fact), and REGARDING (topic). This is \
a complete extraction.

### Example 2: Sales lead report (BAD vs GOOD)

Email from Yash Bhagat: a spreadsheet of 30 individual leads with \
agent names, customer numbers, follow-up dates, and amounts.

BAD output (do NOT do this):
  27 separate Fact triples like "Arshad has a lead for customer \
8653510579" each with STATED_BY → Yash Bhagat. This is row-level \
data extraction — it creates noise without insight.

GOOD output:
{
  "triples": [
    {
      "subject": {"type": "Fact", "description": "Super Hot Leads follow-up report for February 2026: 30 active leads tracked across agents including Arshad, Manoj, Shubham with amounts ranging from Rs. 6000-13999"},
      "predicate": "STATED_BY",
      "object": {"type": "Person", "name": "Yash Bhagat"},
      "evidence_sentence": "Follow Up 23-02-2026"
    },
    {
      "subject": {"type": "Task", "description": "Follow up on Super Hot Leads for February 2026"},
      "predicate": "ASSIGNED_TO",
      "object": {"type": "Person", "name": "Yash Bhagat"},
      "evidence_sentence": "Follow Up 23-02-2026"
    },
    {
      "subject": {"type": "Task", "description": "Follow up on Super Hot Leads for February 2026"},
      "predicate": "REGARDING",
      "object": {"type": "Topic", "name": "Sales Lead Management"},
      "evidence_sentence": "Super Hot Leads"
    }
  ],
  "thread_summary": "Yash Bhagat shared a follow-up report on 30 Super Hot Leads for February 2026, tracking agent-wise performance across multiple sales agents.",
  "department_tags": ["sales"],
  "sensitivity": "internal"
}

### Example 3: Hiring selection letter (COLLECTIVE DECIDER)

Email from hr@ezobooks.in (functional mailbox) selecting Naushad \
Qureshi for Renewal Sales. No individual HR person is named as the \
decider — the decision comes from the HR team as a whole.

"This is to notify you that your application results in your \
selection for the position of Renewal Sales. We expect you to \
join on 22nd April 2025. During your training period, you will \
be paid stipend Rs. 1500 till you are certified (Seven Days)."

GOOD output (use Organization as the collective decider):
{
  "triples": [
    {
      "subject": {"type": "Organization", "name": "EZO HR Team"},
      "predicate": "DECIDED",
      "object": {"type": "Decision", "description": "Selected Naushad Qureshi for the position of Renewal Sales at EZO Technologies"},
      "evidence_sentence": "your application and the subsequent interviews you have done with our company results in your selection for the position of Renewal Sales"
    },
    {
      "subject": {"type": "Decision", "description": "Selected Naushad Qureshi for the position of Renewal Sales at EZO Technologies"},
      "predicate": "SUBJECT_OF",
      "object": {"type": "Person", "name": "Naushad Qureshi"},
      "evidence_sentence": "Dear Naushad Qureshi"
    },
    {
      "subject": {"type": "Decision", "description": "Selected Naushad Qureshi for the position of Renewal Sales at EZO Technologies"},
      "predicate": "BECAUSE",
      "object": {"type": "Fact", "description": "Joining date set for 22nd April 2025, training stipend Rs. 1500 for seven days until certification"},
      "evidence_sentence": "We expect you to join this position on 22nd April 2025 Tuesday. During your training period, you will be paid stipend Rs.1500 till you are certified.( Seven Days)"
    },
    {
      "subject": {"type": "Decision", "description": "Selected Naushad Qureshi for the position of Renewal Sales at EZO Technologies"},
      "predicate": "REGARDING",
      "object": {"type": "Topic", "name": "Hiring"},
      "evidence_sentence": "your application and the subsequent interviews"
    },
    {
      "subject": {"type": "Decision", "description": "Selected Naushad Qureshi for the position of Renewal Sales at EZO Technologies"},
      "predicate": "MENTIONS",
      "object": {"type": "Organization", "name": "EZO Technologies Pvt. Ltd."},
      "evidence_sentence": "Selection Mail From EZO Technologies Pvt. Ltd."
    }
  ],
  "thread_summary": "EZO Technologies selected Naushad Qureshi for the Renewal Sales position with a joining date of 22nd April 2025 and a training stipend of Rs. 1500 for seven days.",
  "department_tags": ["hr"],
  "sensitivity": "confidential"
}

## OUTPUT FORMAT

Return ONLY valid JSON. No markdown, no explanation.

{
  "triples": [
    {
      "subject": {"type": "...", "name": "..." or "description": "..."},
      "predicate": "DECIDED",
      "object": {"type": "...", "name": "..." or "description": "..."},
      "evidence_sentence": "exact quote from email"
    }
  ],
  "thread_summary": "Two sentence summary.",
  "department_tags": ["hr"],
  "sensitivity": "confidential"
}

If no extractable knowledge exists, return:
{"triples": [], "thread_summary": "No extractable knowledge.", \
"department_tags": ["general"], "sensitivity": "internal"}
"""


CORRECTION_PROMPT = """You are correcting a knowledge extraction that \
had validation errors. You will see the original email thread, your \
previous extraction, and the specific errors found.

Common fixes:
- "unresolved_person": Use the EXACT canonical name from the \
THREAD PARTICIPANTS list. Do not invent names or use email addresses.
- "orphan_decision_no_DECIDED_link": A Decision was extracted but \
nobody was identified as the decision-maker. Add a \
(Person|Organization)→DECIDED→Decision triple. If the email comes \
from a functional mailbox (hr@, accounts@, etc.) and no individual \
is named, use an Organization like "EZO HR Team" as the decider.
- "orphan_task_no_ASSIGNED_TO_link": A Task was extracted but nobody \
was assigned. Add a Task→ASSIGNED_TO→(Person|Organization) triple.
- "wrong_subject_type" / "wrong_object_type": The predicate direction \
is wrong. Correct directions:
    DECIDED:         (Person|Org) → Decision
    BECAUSE:         (Decision|Task) → Fact
    STATED_BY:       Fact → (Person|Org)
    ASSIGNED_TO:     Task → (Person|Org)
    REGARDING:       (Decision|Task|Event|Fact) → Topic
    SUBJECT_OF:      Decision → (Person|Org)
    PARTICIPATED_IN: (Person|Org) → Event
    MENTIONS:        (Decision|Fact|Task|Event) → (Person|Org)
- "too_short": Make the description more specific and descriptive \
with concrete details from the email.

Return the COMPLETE corrected extraction — all triples including \
unchanged ones. Return ONLY valid JSON, same format as before.
"""


# ── QUOTE STRIPPING ─────────────────────────────────────────────────

# Gmail: "On Mon, Apr 14, 2026 at 10:30 AM John Smith <john@ex.com> wrote:"
_GMAIL_QUOTE_RE = re.compile(
    r"^On .{10,120} wrote:\s*$", re.MULTILINE)

# Outlook: "-----Original Message-----" or "--- Original Message ---"
_OUTLOOK_QUOTE_RE = re.compile(
    r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE)

# Outlook header block: "From: ...\nSent: ...\nTo: ...\nSubject: ..."
_OUTLOOK_HEADER_RE = re.compile(
    r"^From:\s*.+\n(?:Sent|Date):\s*.+\n(?:To|Cc):\s*.+\nSubject:\s*.+",
    re.MULTILINE | re.IGNORECASE)

# Gmail forwarded: "---------- Forwarded message ----------"
_FORWARDED_RE = re.compile(
    r"^-{5,}\s*Forwarded message\s*-{5,}", re.MULTILINE | re.IGNORECASE)


def strip_quoted_replies(body: str) -> str:
    """Strip quoted reply blocks from email body to reduce token waste.

    Gmail threads repeat the entire prior conversation in each reply.
    Stripping these saves 30-40% tokens on reply-heavy threads without
    losing information (earlier emails are already included separately
    in the thread prompt).
    """
    if not body:
        return body

    # Find the earliest quote marker and truncate there
    earliest_pos = len(body)

    for pattern in (_GMAIL_QUOTE_RE, _OUTLOOK_QUOTE_RE,
                    _OUTLOOK_HEADER_RE, _FORWARDED_RE):
        match = pattern.search(body)
        if match and match.start() < earliest_pos:
            earliest_pos = match.start()

    if earliest_pos < len(body):
        stripped = body[:earliest_pos].rstrip()
        # Only use stripped version if meaningful content remains
        if len(stripped) >= 20:
            return stripped

    # Fallback: if >50% of lines are > quoted, keep only non-quoted
    lines = body.split("\n")
    quoted = sum(1 for l in lines if l.strip().startswith(">"))
    if quoted > len(lines) * 0.5:
        non_quoted = [l for l in lines if not l.strip().startswith(">")]
        result = "\n".join(non_quoted).strip()
        if len(result) >= 20:
            return result

    return body


# ── ORG CONTEXT ─────────────────────────────────────────────────────

def build_org_context(persons_by_id: dict) -> str:
    """Build a brief organizational context block for the LLM prompt.

    Lists key internal people by communication volume so the LLM
    understands who the decision-makers and key operators are.
    Built once per extraction run and prepended to every thread prompt.
    Costs ~150-200 extra tokens per call but improves person attribution.
    """
    internal_persons = []
    for person in persons_by_id.values():
        if not person.get("is_internal", False):
            continue
        if person.get("is_functional", False):
            continue
        stats = person.get("stats", {})
        activity = stats.get("total_activity", 0)
        name = person.get("canonical_name", "")
        if name and activity > 0:
            internal_persons.append((name, activity))

    if not internal_persons:
        return ""

    internal_persons.sort(key=lambda x: -x[1])
    top = internal_persons[:20]

    lines = ["ORGANIZATIONAL CONTEXT (key internal people by email volume):"]
    for name, activity in top:
        if activity >= 1000:
            level = "very high"
        elif activity >= 500:
            level = "high"
        elif activity >= 100:
            level = "moderate"
        else:
            level = "low"
        lines.append(f"  {name} — {activity} emails ({level} activity)")

    return "\n".join(lines)


# ── PROMPT BUILDING ─────────────────────────────────────────────────

def _get_sender_name(event: dict, email_to_person: dict,
                     persons_by_id: dict) -> str:
    """Resolve display name for the primary actor of an email."""
    addr = event.get("primary_actor", "").replace("email:", "").lower()

    # Person registry first (canonical name)
    pid = email_to_person.get(addr)
    if pid and pid in persons_by_id:
        return persons_by_id[pid].get("canonical_name", addr)

    # Fallback to actor's display_name in the event itself
    for actor in event.get("actors", []):
        actor_addr = (actor.get("email")
                      or actor.get("id", "").replace("email:", "")).lower()
        if actor_addr == addr and actor.get("display_name"):
            return actor["display_name"]

    return addr


# ── PER-SOURCE FRAMING HINTS ────────────────────────────────────────
# Prepended to the per-thread user message. The ontology + base examples live in
# SYSTEM_PROMPT (email-shaped); these adapt the FRAMING per source without bloating
# the system prompt for every call. Selected by SOURCE_HINTS — no inline if/elif.

JIRA_HINT = (
    "NOTE: This is a Jira issue thread. The first block is the issue "
    "description (written by the reporter). Subsequent blocks are comments. "
    "Attribute DECIDED/STATED_BY triples to the person explicitly named as "
    "making the decision or statement — NOT to the issue reporter by default. "
    "The assignee owns ASSIGNED_TO tasks unless stated otherwise.\n\n"
)

SLACK_HINT = (
    "NOTE: This is a Slack channel conversation — a time-window SESSION of "
    "messages, not an email thread. Messages are short and informal, and a single "
    "Decision or Task is often spread across several messages (the resolution may "
    "be in a later message or a thread reply). Read the whole session together "
    "before extracting, and attribute each triple to the person who actually said "
    "it.\n"
    "IGNORE as non-knowledge: reactions, acknowledgements and chit-chat (\"+1\", "
    "\"lol\", \"ok\", \"thanks\", \"on it\", emoji-only), greetings, and channel "
    "join/leave lines — never turn these into Facts.\n"
    "If the session contains no decision, task, or concrete institutional fact, "
    "return empty triples.\n\n"
    "EXAMPLE — a decision reached across messages:\n"
    "  @ravi: should we move billing to Razorpay or stay on Stripe?\n"
    "  @meera: Razorpay's fees are lower for India and UPI works out of the box.\n"
    "  @ravi: ok let's switch billing to Razorpay then.\n"
    "GOOD output:\n"
    '{"triples":[\n'
    '  {"subject":{"type":"Person","name":"Ravi"},"predicate":"DECIDED",'
    '"object":{"type":"Decision","description":"Switch billing from Stripe to '
    'Razorpay"},"evidence_sentence":"ok let\'s switch billing to Razorpay then"},\n'
    '  {"subject":{"type":"Decision","description":"Switch billing from Stripe to '
    'Razorpay"},"predicate":"BECAUSE","object":{"type":"Fact","description":'
    '"Razorpay has lower fees for India and native UPI support"},'
    '"evidence_sentence":"Razorpay\'s fees are lower for India and UPI works out '
    'of the box"},\n'
    '  {"subject":{"type":"Decision","description":"Switch billing from Stripe to '
    'Razorpay"},"predicate":"REGARDING","object":{"type":"Topic","name":"Billing '
    'Provider"},"evidence_sentence":"move billing to Razorpay"}\n'
    "]}\n\n"
    "EXAMPLE — chit-chat only (no knowledge):\n"
    "  @ravi: morning all   @meera: 🎉   @sam: lol same\n"
    'GOOD output: {"triples":[],"thread_summary":"No extractable knowledge.",'
    '"department_tags":["general"],"sensitivity":"internal"}\n\n'
)

# The ONLY place a source attaches a framing hint. Unknown source → no hint.
SOURCE_HINTS: dict[str, str] = {"jira": JIRA_HINT, "slack": SLACK_HINT}


def build_thread_prompt(thread: dict, email_to_person: dict,
                        persons_by_id: dict,
                        org_context: str = "") -> str:
    """Build the user message for triple extraction."""
    emails = thread["emails"]
    thread_id = thread["thread_id"]
    count = len(emails)

    # ── Collect all participants ──────────────────────────────────────
    participants: set[str] = set()
    for email in emails:
        sender = email.get("primary_actor", "").replace("email:", "").lower()
        if sender:
            participants.add(sender)
        for actor in email.get("actors", []):
            addr = (actor.get("email")
                    or actor.get("id", "").replace("email:", "")).lower()
            if addr:
                participants.add(addr)

    participant_lines = []
    for addr in sorted(participants):
        pid = email_to_person.get(addr)
        if pid and pid in persons_by_id:
            person = persons_by_id[pid]
            name = person.get("canonical_name", addr)
            is_internal = person.get("is_internal", False)
            is_functional = person.get("is_functional", False)
            label = "INTERNAL" if is_internal else "EXTERNAL"
            if is_functional:
                label += ", FUNCTIONAL MAILBOX"
            participant_lines.append(f"  {name} ({addr}) — {label}")
        else:
            participant_lines.append(f"  {addr} — EXTERNAL, UNKNOWN")

    participant_ctx = "THREAD PARTICIPANTS:\n" + "\n".join(participant_lines)

    # Source drives framing: Slack = channel session (no subject line, "Message"
    # units); Jira = issue thread; Gmail = email thread.
    source = (emails[0].get("source", "") if emails else "")
    is_slack = source == "slack"
    unit = "Message" if is_slack else "Email"

    # ── Build content blocks ──────────────────────────────────────────
    email_blocks: list[str] = []
    for i, email in enumerate(emails):
        sender_name = _get_sender_name(email, email_to_person, persons_by_id)
        ts = email.get("timestamp", "")[:16]
        body = strip_quoted_replies(email.get("body", "") or "")
        # Full body per message — quote stripping removed redundant content. Prompt
        # size is bounded upstream (Gmail size-buckets; Slack session_max_messages),
        # which matters now that the model is deepseek-v3.2 (smaller context).
        if is_slack:
            # Slack has no real subject — title is just the body prefix; omit it.
            email_blocks.append(
                f"--- {unit} {i+1} of {count} ---\n"
                f"From: {sender_name}\nTime: {ts}\n\n{body}")
        else:
            subject = email.get("title", "No subject")
            email_blocks.append(
                f"--- {unit} {i+1} of {count} ---\n"
                f"From: {sender_name}\nDate: {ts}\n"
                f"Subject: {subject}\n\n{body}")

    thread_content = "\n\n".join(email_blocks)
    subject = emails[0].get("title", "No subject") if emails else "No subject"
    header = f"{org_context}\n\n" if org_context else ""

    # Source / ACL metadata — included when present
    meta_lines: list[str] = []
    if source:
        meta_lines.append(f"SOURCE: {source}")
    if thread.get("scope_id"):
        meta_lines.append(f"SCOPE_ID: {thread.get('scope_id')}")
    if thread.get("source_ref"):
        meta_lines.append(f"SOURCE_REF: {thread.get('source_ref')}")
    meta_block = "\n".join(meta_lines) + "\n\n" if meta_lines else ""

    # Per-source framing hint (parallel to the ontology in SYSTEM_PROMPT).
    hint = SOURCE_HINTS.get(source, "")

    if is_slack:
        head_block = f"CHANNEL SESSION: {thread_id}\nMESSAGE COUNT: {count}\n\n"
        closer = ("Extract all graph triples from this Slack channel session. "
                  "If it contains no decision, task, or concrete fact, return "
                  "empty triples. Return valid JSON only.")
    else:
        head_block = (f"THREAD ID: {thread_id}\nSUBJECT: {subject}\n"
                      f"{unit.upper()} COUNT: {count}\n\n")
        closer = "Extract all graph triples from this thread. Return valid JSON only."

    return (
        f"{header}"
        f"{hint}"
        f"{participant_ctx}\n\n"
        f"{meta_block}"
        f"{head_block}"
        f"{thread_content}\n\n"
        f"{closer}"
    )
