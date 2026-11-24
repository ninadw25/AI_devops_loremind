"""
pipeline/validation/id_minting.py
─────────────────────────────────
Deterministic node-ID assignment for validated triples.

Delegates to `common.hashing.node_id_for` so the builders'
post-processor and this stage share the same ID formula.
"""

from __future__ import annotations

from common.hashing import node_id_for


_SCOPED_NODE_TYPES = {"Decision", "Task", "Fact", "Event"}
# Jira types are name-keyed (Issue key/accountId/sprint name are globally stable).
_NAME_KEYED_NODE_TYPES = {"Person", "Organization", "Topic", "Issue", "Sprint", "Component", "Version"}


def _assign_node_ids(triple: dict, thread_id: str):
    """Assign deterministic node IDs to subject and object.

    Delegates the formula to `common.hashing.node_id_for` so the
    builders' post-processor and this validator stay in lockstep.
    """
    for node_key in ("subject", "object"):
        node = triple[node_key]
        ntype = node.get("type", "")
        name = node.get("name") or node.get("description") or ""

        if ntype in _NAME_KEYED_NODE_TYPES:
            node["node_id"] = node_id_for(ntype, name)
        elif ntype in _SCOPED_NODE_TYPES:
            node["node_id"] = node_id_for(ntype, name, scope=thread_id)
