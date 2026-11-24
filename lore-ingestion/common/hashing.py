"""
common/hashing.py
─────────────────
Deterministic hashing for stable node ID generation.
Shared by ingestion (Neo4j graph builder) and backend (vector index).

`node_id_for(node_type, name, scope=None)` is the single owner of the
node-ID formula. Two patterns are supported:

  Name-keyed (Person, Organization, Topic, Process)
      ID = "{prefix}:{short_hash(name.lower())}"
      Stable across threads — same display name → same ID everywhere.

  Scoped (Decision, Task, Fact, Event)
      ID = "{prefix}:{short_hash(scope + name)}"
      Unique per scope (typically thread_id) — same "approve budget"
      decision in two threads produces two different IDs.

`HASH_VERSION` is bumped whenever the formula changes. Consumers that
care about cross-system ID stability (Neo4j ↔ vector index) should
record the version they were built against.
"""

import hashlib


HASH_VERSION = "v1"


def short_hash(text, length=12):
    """Deterministic short hash for IDs (SHA-256, first N hex chars)."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:length]


def make_id(source: str, source_id: str) -> str:
    """Stable ID generation from source + source_id."""
    raw = f"{source}:{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# Node types whose ID is keyed on the lowercased name (stable across scopes).
_NAME_KEYED_PREFIXES = {
    "Person":       "person",
    "Organization": "org",
    "Topic":        "topic",
    "Process":      "proc",
    # Jira structured nodes — all have globally stable keys.
    # Issue key is passed as "(org):(jira_key)", e.g. "loremind:ENG-123".
    # Sprint/Component/Version use "(project):(name)" for uniqueness.
    "Issue":        "issue",
    "Sprint":       "sprint",
    "Component":    "component",
    "Version":      "version",
}

# Node types whose ID is keyed on `scope + name` (typically thread-scoped).
_SCOPED_PREFIXES = {
    "Decision": "dec",
    "Task":     "task",
    "Fact":     "fact",
    "Event":    "evt",
}


def node_id_for(node_type: str, name: str, scope: str | None = None) -> str:
    """Mint a deterministic node ID for a node of the given type.

    Args:
        node_type: One of Person, Organization, Topic, Process,
                   Decision, Task, Fact, Event.
        name:      The node's display name or description.
        scope:     Required for scoped types (Decision/Task/Fact/Event).
                   Typically a thread_id, but any string that disambiguates
                   the node within its context works. Ignored for
                   name-keyed types.

    Returns:
        "{prefix}:{12-char SHA-256 hex}" — e.g. "person:a1b2c3d4e5f6".

    Raises:
        ValueError: If node_type is unknown, or if scope is missing
                    for a scoped type.
    """
    if node_type in _NAME_KEYED_PREFIXES:
        prefix = _NAME_KEYED_PREFIXES[node_type]
        return f"{prefix}:{short_hash(name.lower())}"
    if node_type in _SCOPED_PREFIXES:
        if scope is None:
            raise ValueError(
                f"scope is required for node type {node_type!r}")
        prefix = _SCOPED_PREFIXES[node_type]
        return f"{prefix}:{short_hash(scope + name)}"
    raise ValueError(f"Unknown node type: {node_type!r}")


def make_slack_id(org: str, channel_id: str, ts: str) -> str:
    """Convenience wrapper to produce a stable Slack event id.

    Uses the existing `make_id` formula with source='slack' and
    source_id='{org}:{channel_id}:{ts}'. Returns a short 16-char hex id.
    """
    return make_id("slack", f"{org}:{channel_id}:{ts}")
