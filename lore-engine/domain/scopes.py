"""
domain/scopes.py
────────────────
Single owner of the fail-closed content-scope gate for the unstructured
query path (Gmail / Slack / Outlook / Teams). See docs/QUERY_PIPELINE_V2.md §4.

Every retrieval Cypher in domain/retrieval/{seeds,frontier,rank}.py builds its
scope predicate from scope_gate() here — never by hand. One owner means one
place to audit the locked ACL semantics:

    a node is visible to a caller iff
        it has at least one scope_id            (empty/absent → invisible)
        AND at least one of its scope_ids is in the caller's allowed set   (OR-set)
        AND all of its scope_all_ids are in the caller's allowed set       (AND-set)

Fail-closed both directions, with no global override:
  - a node with no scope_ids is invisible to everyone, and
  - a caller with no allowed scopes sees nothing.

The only way to read past this gate is the content super-admin bypass — the
`content.read_all` authority, resolved per request by the gateway into the
$super_admin param. That is per-user, tenant-bounded and audited. There is
deliberately no deploy-time flag that opens the gate globally.

Spine nodes (Person / Organization / Topic) carry no scopes by design — they
are the cross-source MERGE spine. They are traversable but never gated and
never citable; content reached *through* them is still gated. Callers gate
content labels only (see frontier.py).

The Cypher fragment and the Python mirror (is_visible) implement the SAME
predicate so defense-in-depth filtering in Python cannot diverge from the gate
Neo4j applies.
"""

from __future__ import annotations

from core.settings import get_settings
from core.types import UserScopes

# Name of the Cypher parameter every scoped query binds the allowed-scope
# list to. Bound via scope_params().
SCOPE_PARAM = "allowed_scopes"

# Name of the Cypher parameter carrying the per-request content super-admin
# bypass. Bound via scope_params() alongside SCOPE_PARAM, so every scoped query
# that spreads scope_params() binds it automatically. True ⇒ the scope gate
# short-circuits to visible for this request. See docs/CONTENT_SUPERADMIN.md.
SUPER_ADMIN_PARAM = "super_admin"


def scope_gate(var: str = "node") -> str:
    """Return the boolean Cypher fragment gating node variable `var`.

    Interpolate into a WHERE clause, e.g.:

        f"WHERE {scope_gate('m')} AND m.created_at >= $since"

    The fragment references only `var`, the $allowed_scopes parameter, and the
    $super_admin bypass flag — so it is safe to AND with any other conditions.
    Both parameters are bound together by scope_params(); a query that uses
    scope_gate() MUST spread scope_params() (they all do).

    The $super_admin disjunct is the legitimate per-request bypass: when the
    gateway sets it (caller holds `content.read_all`), every node is visible for
    this query only. The fail-closed membership predicate is otherwise unchanged.
    """
    return (
        f"( ${SUPER_ADMIN_PARAM} OR ( "
        f"size(coalesce({var}.scope_ids, [])) > 0 "
        f"AND any(s IN {var}.scope_ids WHERE s IN ${SCOPE_PARAM}) "
        f"AND all(s IN coalesce({var}.scope_all_ids, []) WHERE s IN ${SCOPE_PARAM}) ) )"
    )


# Scope-id prefix that marks a node as Jira-derived. The unstructured query
# path (Gmail/Slack/Outlook/Teams) must never serve these — Jira has its own
# retrieval path (domain/retrieval/jira.py), kept deliberately separate
# (LOR-63). Content nodes carry no `source` property, so the scope_id prefix is
# the only reliable source discriminator. GitHub/GitLab live in a separate
# store, so "any non-jira scope" == one of the four unstructured sources.
_JIRA_PREFIX = "jira:"


def unstructured_source_filter(var: str = "node") -> str:
    """Cypher fragment keeping only Gmail/Slack/Outlook/Teams nodes.

    True iff the node has at least one non-Jira scope_id. AND this with
    scope_gate() in every seed/traversal query so the unstructured path and
    the Jira path never serve the same node.
    """
    return f"any(s IN coalesce({var}.scope_ids, []) WHERE NOT s STARTS WITH '{_JIRA_PREFIX}')"


def jira_source_filter(var: str = "node") -> str:
    """Cypher fragment keeping only Jira-derived nodes — the mirror of
    unstructured_source_filter(). The Jira engine's vector search over the
    shared Decision/Fact space must AND this in, so it never pulls in
    Gmail/Slack/Outlook/Teams knowledge (which the unstructured engine owns).
    """
    return f"any(s IN coalesce({var}.scope_ids, []) WHERE s STARTS WITH '{_JIRA_PREFIX}')"


def is_unstructured(scope_ids: list[str] | None) -> bool:
    """Python mirror of unstructured_source_filter()."""
    return any(not s.startswith(_JIRA_PREFIX) for s in (scope_ids or []))


# Scope-id prefix → human source label, for tagging SourcedChunk.source.
# Content nodes carry no `source` property, so this is derived from scopes.
_SOURCE_PREFIXES: list[tuple[str, str]] = [
    ("slack:", "slack"),
    ("teams:", "teams"),
    ("ms365:", "teams"),
    ("outlook:", "outlook"),
    ("jira:", "jira"),
]


def source_label(scope_ids: list[str] | None) -> str:
    """Best-effort source name from a node's scope_ids. Gmail scopes are
    participant person ids with no source prefix, so they fall through to
    'gmail' (the default unstructured source)."""
    for s in (scope_ids or []):
        for prefix, label in _SOURCE_PREFIXES:
            if s.startswith(prefix):
                return label
    return "gmail"


def scope_params(scopes: UserScopes) -> dict:
    """Build the parameter dict carrying the caller's allowed scopes plus the
    content super-admin bypass flag.

    Spread into every scoped client.run() call:  client.run(cypher, **scope_params(scopes), ...)
    Binds both $allowed_scopes and $super_admin, which scope_gate() references.
    """
    return {
        SCOPE_PARAM: list(scopes.allowed),
        SUPER_ADMIN_PARAM: bool(scopes.super_admin),
    }


def is_visible(
    scope_ids: list[str] | None,
    scope_all_ids: list[str] | None,
    allowed: list[str],
    super_admin: bool = False,
) -> bool:
    """Python mirror of scope_gate(), for defense-in-depth filtering of rows
    Neo4j already gated. Same predicate, so it can only ever agree with Cypher.

    super_admin mirrors the $super_admin bypass: True ⇒ visible regardless of
    scopes (the caller holds content.read_all).
    """
    if super_admin:
        return True  # content super-admin bypass
    ids = scope_ids or []
    if not ids:
        return False  # fail-closed: no scopes → invisible
    allowed_set = set(allowed)
    if not any(s in allowed_set for s in ids):
        return False
    return all(s in allowed_set for s in (scope_all_ids or []))
