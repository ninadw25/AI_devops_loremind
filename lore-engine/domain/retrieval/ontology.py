"""
domain/retrieval/ontology.py
─────────────────────────────
Single owner of the graph-shape constants the v2 unstructured retrieval path
relies on, kept in sync with lore-ingestion/builders/unified_builder.py and
common/hashing.py. Centralised so seeds.py, frontier.py, and rank.py never
hard-code label/id-field/relation knowledge.

Ground truth (verified 2026-06-12):
  - Content knowledge nodes carry a :KnowledgeNode secondary label and a
    TYPE-SPECIFIC id property under a unique constraint. There is NO unified
    `node_id` property. Id VALUES are type-prefixed.
  - Spine nodes (Person/Organization/Topic) are name-keyed, shared across
    sources (the MERGE spine), carry no scopes, and are never citable content.
"""

from __future__ import annotations

# Content node labels carrying embeddings + scopes (the citable knowledge).
CONTENT_LABELS: tuple[str, ...] = ("Decision", "Task", "Fact", "Event")

# Spine labels: traversable, never gated, never citable (no content/scopes).
SPINE_LABELS: tuple[str, ...] = ("Person", "Organization", "Topic")

# Label → its unique-constrained id property (the indexed match key).
ID_FIELD: dict[str, str] = {
    "Decision": "decision_id", "Task": "task_id", "Fact": "fact_id",
    "Event": "event_id", "Person": "person_id", "Organization": "org_id",
    "Topic": "topic_id",
}

# Id-value prefix → label (values look like "dec:ab12…", "person:cd34…").
# Lets us recover a node's type from its id string alone.
_PREFIX_TO_LABEL: dict[str, str] = {
    "dec": "Decision", "task": "Task", "fact": "Fact", "evt": "Event",
    "person": "Person", "org": "Organization", "topic": "Topic",
}

# All labels we ever match/traverse on this path (content + spine).
TRAVERSABLE_LABELS: tuple[str, ...] = CONTENT_LABELS + SPINE_LABELS


def label_for_id(node_id: str) -> str | None:
    """Recover a node's label from its type-prefixed id, or None if unknown
    (e.g. a Jira `issue:`/`sprint:` id, which this path does not serve)."""
    prefix = node_id.split(":", 1)[0] if ":" in node_id else ""
    return _PREFIX_TO_LABEL.get(prefix)


def node_id_coalesce(var: str) -> str:
    """Cypher expression yielding a node's stable id regardless of type.

    Safe on a RETURN projection (the node is already bound by traversal, so no
    index/scan is involved). Covers content + spine; a Jira node returns null
    and is dropped downstream.
    """
    fields = ["decision_id", "task_id", "fact_id", "event_id",
              "person_id", "topic_id", "org_id"]
    inner = ", ".join(f"{var}.{f}" for f in fields)
    return f"coalesce({inner})"
