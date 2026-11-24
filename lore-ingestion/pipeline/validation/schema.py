"""
pipeline/validation/schema.py
─────────────────────────────
Schema constants for triple validation.

`PREDICATE_SCHEMA` is the single source of truth for which subject and
object node types each predicate accepts. The keys are bound to the
`Predicate` enum so they cannot silently drift if the enum changes.
"""

from __future__ import annotations

import re

from common.schemas_extraction import NodeType, Predicate


ALLOWED_NODE_TYPES = {t.value for t in NodeType}

# predicate → (allowed_subject_types, allowed_object_types).
PREDICATE_SCHEMA = {
    # ── Shared (email + Jira LLM prose) ──────────────────────────────────────
    # DECIDED: decisions can be made by named individuals OR collective
    # actors (HR teams, client organizations, functional mailboxes).
    Predicate.DECIDED.value:         ({"Person", "Organization"}, {"Decision"}),

    # BECAUSE: rationale can ground Decisions AND Tasks
    # ("follow up with Razorpay because ticket is open").
    Predicate.BECAUSE.value:         ({"Decision", "Task"}, {"Fact"}),

    Predicate.STATED_BY.value:       ({"Fact"},             {"Person", "Organization"}),
    # ASSIGNED_TO: Task→Person for email; Issue→Person for Jira (deterministic).
    Predicate.ASSIGNED_TO.value:     ({"Task", "Issue"},    {"Person", "Organization"}),

    # REGARDING: any knowledge-bearing node can have a topic.
    # Facts/Events stand on their own and deserve direct topic links.
    # Issue→Topic for Jira LLM prose mentions of topic areas.
    Predicate.REGARDING.value:       ({"Decision", "Task", "Event", "Fact", "Issue"}, {"Topic"}),

    # SUBJECT_OF: the affected party may be a person or an organization.
    Predicate.SUBJECT_OF.value:      ({"Decision"},         {"Person", "Organization"}),

    Predicate.WORKS_AT.value:        ({"Person"},           {"Organization"}),

    # PARTICIPATED_IN: organizations participate in events too.
    Predicate.PARTICIPATED_IN.value: ({"Person", "Organization"}, {"Event"}),

    # MENTIONS: Jira adds Issue as a possible target (cross-issue references in prose).
    Predicate.MENTIONS.value:        ({"Decision", "Fact", "Task", "Event"},
                                      {"Organization", "Person", "Issue"}),

    # ── Jira deterministic edges (from structured fields, derived="field") ───
    # These are trusted — validate_triple passes them through without LLM checks.
    Predicate.REPORTED_BY.value:     ({"Issue"},            {"Person"}),
    Predicate.PART_OF_EPIC.value:    ({"Issue"},            {"Issue"}),
    Predicate.SUBTASK_OF.value:      ({"Issue"},            {"Issue"}),
    Predicate.BLOCKS.value:          ({"Issue"},            {"Issue"}),
    Predicate.BLOCKED_BY.value:      ({"Issue"},            {"Issue"}),
    Predicate.RELATES_TO.value:      ({"Issue"},            {"Issue"}),
    Predicate.DUPLICATES.value:      ({"Issue"},            {"Issue"}),
    Predicate.IN_SPRINT.value:       ({"Issue"},            {"Sprint"}),
    Predicate.HAS_COMPONENT.value:   ({"Issue"},            {"Component"}),
    Predicate.FIX_IN_VERSION.value:  ({"Issue"},            {"Version"}),
    Predicate.LABELED.value:         ({"Issue"},            {"Topic"}),
    Predicate.RESOLVED_BY.value:     ({"Issue"},            {"Person"}),

    # ── Jira provenance edge (added programmatically, not by LLM) ────────────
    # Anchors every LLM-derived node to the Issue whose prose produced it.
    Predicate.DERIVED_FROM.value:    ({"Decision", "Fact", "Topic"}, {"Issue"}),
}

# Content quality patterns — garbage indicators
_HTML_RE = re.compile(r"<[^>]{2,}>")
_CHAT_PASTE_RE = re.compile(r"\[\d{1,2}/\d{1,2},?\s+\d{1,2}:\d{2}")
_CSS_RE = re.compile(r"(font-size|margin|padding|color)\s*:")
_PLACEHOLDER_RE = re.compile(
    r"^(n/a|none|null|unknown|not specified|not stated|unclear|"
    r"no .{0,20}(found|specified|stated|available))$",
    re.IGNORECASE,
)

MIN_DESCRIPTION_LENGTH = 8
MIN_EVIDENCE_LENGTH = 10
