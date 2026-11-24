"""
pipeline/jira/deterministic.py
───────────────────────────────
Stage 1 of the Jira extraction split: deterministic field→triple builder.

Reads the normalized KnowledgeEvent (issue + its comment/changelog events
already grouped into a thread dict) and emits structured triples purely from
Jira fields — no LLM, no rate-limit cost. Output shape is identical to what
the LLM produces so the validator, embed, and builder consume it unchanged.

Every triple is tagged `derived="field"` which causes validate_triple to pass
it through without LLM-quality checks (content length, person-name resolution,
etc.) since the data came from authoritative structured fields.

Design: JIRA_EXTRACTION_AND_DEDUP.md §A3 / JIRA_ONTOLOGY.md §3a/3b.
"""

from __future__ import annotations

from typing import Any

from common.hashing import node_id_for
from common.rbac import ALLOWED_DEPARTMENTS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _issue_node(key: str, org: str, fields: dict) -> dict:
    """Build a NodeRef dict for an Issue node."""
    node_id = node_id_for("Issue", f"{org}:{key}")
    return {
        "type": "Issue",
        "name": key,
        "node_id": node_id,
        # Extra properties stored for the builder; not in NodeRef schema but
        # passed through as the dict flows to Neo4j Cypher generation.
        "_issue_type": (fields.get("issuetype") or {}).get("name", ""),
        "_status": (fields.get("status") or {}).get("name", ""),
        "_priority": (fields.get("priority") or {}).get("name"),
        "_summary": (fields.get("summary") or "")[:200],
        "_created": fields.get("created"),
    }


def _person_node(person: dict) -> dict:
    """Build a NodeRef dict for a Person node, keyed by accountId."""
    account_id = person.get("accountId", "")
    display_name = person.get("displayName") or account_id
    node_id = node_id_for("Person", account_id)
    return {
        "type": "Person",
        "name": account_id,          # stable key for dedup
        "_display_name": display_name,
        "_account_type": person.get("accountType", ""),
        "_email": person.get("emailAddress", ""),
        "node_id": node_id,
    }


def _issue_ref(key: str, org: str) -> dict:
    """Minimal Issue NodeRef for the target of an issue-to-issue edge."""
    return {
        "type": "Issue",
        "name": key,
        "node_id": node_id_for("Issue", f"{org}:{key}"),
    }


def _sprint_node(project: str, sprint_name: str) -> dict:
    composite = f"{project}:{sprint_name}"
    return {
        "type": "Sprint",
        "name": composite,
        "node_id": node_id_for("Sprint", composite),
        "_sprint_name": sprint_name,
    }


def _component_node(project: str, comp_name: str) -> dict:
    composite = f"{project}:{comp_name}"
    return {
        "type": "Component",
        "name": composite,
        "node_id": node_id_for("Component", composite),
        "_component_name": comp_name,
    }


def _version_node(project: str, ver_name: str) -> dict:
    composite = f"{project}:{ver_name}"
    return {
        "type": "Version",
        "name": composite,
        "node_id": node_id_for("Version", composite),
        "_version_name": ver_name,
    }


def _triple(subject: dict, predicate: str, obj: dict, evidence: str) -> dict:
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "evidence_sentence": evidence,
        "derived": "field",
    }


# ── Issuelink parsing ─────────────────────────────────────────────────────────

_INWARD_TO_PRED: dict[str, str] = {
    "is blocked by": "BLOCKED_BY",
    "is duplicated by": "DUPLICATES",
    "is related to": "RELATES_TO",
    "blocks": "BLOCKS",
    "duplicates": "DUPLICATES",
    "relates to": "RELATES_TO",
    "clones": "RELATES_TO",
    "is cloned by": "RELATES_TO",
}
_OUTWARD_TO_PRED: dict[str, str] = {
    "blocks": "BLOCKS",
    "duplicates": "DUPLICATES",
    "relates to": "RELATES_TO",
    "is blocked by": "BLOCKED_BY",
    "clones": "RELATES_TO",
}


def _issuelink_triples(issue_node: dict, links: list, org: str) -> list[dict]:
    triples: list[dict] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        link_type = (link.get("type") or {}).get("name", "").lower()
        inward_type = (link.get("type") or {}).get("inward", "").lower()
        outward_type = (link.get("type") or {}).get("outward", "").lower()

        # Outward link: this issue → linked issue
        outward_issue = link.get("outwardIssue") or {}
        if outward_issue.get("key"):
            pred = (_OUTWARD_TO_PRED.get(outward_type)
                    or _OUTWARD_TO_PRED.get(link_type)
                    or "RELATES_TO")
            triples.append(_triple(
                issue_node, pred, _issue_ref(outward_issue["key"], org),
                f"Issuelink ({outward_type or link_type}): {outward_issue['key']}",
            ))

        # Inward link: this issue ← linked issue (reverse direction)
        inward_issue = link.get("inwardIssue") or {}
        if inward_issue.get("key"):
            pred = (_INWARD_TO_PRED.get(inward_type)
                    or _INWARD_TO_PRED.get(link_type)
                    or "RELATES_TO")
            triples.append(_triple(
                issue_node, pred, _issue_ref(inward_issue["key"], org),
                f"Issuelink ({inward_type or link_type}): {inward_issue['key']}",
            ))
    return triples


# ── Changelog: find who resolved the issue ───────────────────────────────────

def _find_resolver(events: list[dict]) -> dict | None:
    """Find the Person who resolved/closed the issue from changelog events."""
    for event in events:
        if event.get("event_type") not in ("jira.issue.resolved", "jira.issue.transitioned"):
            continue
        raw = event.get("raw") or {}
        items = raw.get("items") or []
        for item in items:
            field = item.get("field", "")
            to_str = (item.get("toString") or "").lower()
            if field in ("resolution", "status") and to_str in ("done", "resolved", "closed", "fixed"):
                author = raw.get("author") or {}
                if author.get("accountId"):
                    return author
    return None


# ── Department inference ──────────────────────────────────────────────────────

_PROJECT_TO_DEPT: dict[str, str] = {
    "HR": "hr",
    "ENG": "engineering",
    "EDGE": "engineering",
    "OPS": "operations",
    "SALES": "sales",
    "FIN": "finance",
    "LEGAL": "legal",
    "MKTG": "marketing",
    "PROD": "product",
    "SCRUM": "engineering",
}


def _infer_dept_tags(project: str, tags: list[str]) -> list[str]:
    dept = _PROJECT_TO_DEPT.get(project.upper() if project else "")
    candidates = [dept] if dept else []
    for tag in tags:
        t = tag.lower()
        if t in ALLOWED_DEPARTMENTS:
            candidates.append(t)
    return list(dict.fromkeys(candidates)) or ["general"]


# ── Public API ────────────────────────────────────────────────────────────────

def build_deterministic_triples(thread: dict, org: str = "") -> dict:
    """Build structured triples from Jira fields for one issue thread.

    Args:
        thread: Grouped thread dict from group_by_thread — contains
                "emails" (issue + comment + changelog events), "thread_id"
                (the Jira key), scope_id, etc.
        org:    Org slug used to namespace Issue node IDs (avoids collisions
                when multiple orgs share key sequences like ENG-1).

    Returns:
        An ExtractionResult-compatible dict with all deterministic triples
        tagged derived="field". Ready to merge with the LLM prose result.
    """
    thread_id = thread.get("thread_id", "")
    events = thread.get("emails", [])

    # Find the root issue event — it's the one whose source_id == thread_id
    # (parent_id is None or absent for root issues).
    root: dict | None = None
    for ev in events:
        if ev.get("source_id") == thread_id and ev.get("event_type", "").startswith("jira.issue") or \
           (ev.get("parent_id") is None and ev.get("source") == "jira" and not ev.get("event_type", "").startswith("jira.comment") and not ev.get("event_type", "").startswith("jira.issue.transitioned") and not ev.get("event_type", "").startswith("jira.issue.resolved") and not ev.get("event_type", "").startswith("jira.issue.reassigned")):
            root = ev
            break
    # Fallback: first event in thread
    if root is None and events:
        root = events[0]
    if root is None:
        return _empty_result(thread_id, org)

    raw = root.get("raw") or {}
    fields = raw.get("fields") or {}
    issue_key = root.get("source_id") or raw.get("key") or thread_id
    project = root.get("project") or (fields.get("project") or {}).get("key") or ""

    issue_node = _issue_node(issue_key, org, fields)
    triples: list[dict] = []

    # ── REPORTED_BY ───────────────────────────────────────────────────────────
    reporter = fields.get("reporter") or {}
    if reporter.get("accountId"):
        triples.append(_triple(
            issue_node, "REPORTED_BY", _person_node(reporter),
            f"Reporter: {reporter.get('displayName', reporter['accountId'])}",
        ))

    # ── ASSIGNED_TO ───────────────────────────────────────────────────────────
    assignee = fields.get("assignee") or {}
    if assignee.get("accountId"):
        triples.append(_triple(
            issue_node, "ASSIGNED_TO", _person_node(assignee),
            f"Assignee: {assignee.get('displayName', assignee['accountId'])}",
        ))

    # ── PART_OF_EPIC / SUBTASK_OF ─────────────────────────────────────────────
    issue_type_name = (fields.get("issuetype") or {}).get("name", "")
    for epic_key in (root.get("related_ids") or []):
        if epic_key and epic_key != issue_key:
            triples.append(_triple(
                issue_node, "PART_OF_EPIC", _issue_ref(epic_key, org),
                f"Epic link: {epic_key}",
            ))

    parent_key = root.get("parent_id")
    if parent_key and parent_key not in (root.get("related_ids") or []):
        pred = "SUBTASK_OF" if issue_type_name == "Sub-task" else "PART_OF_EPIC"
        triples.append(_triple(
            issue_node, pred, _issue_ref(parent_key, org),
            f"Parent: {parent_key}",
        ))

    # ── Issuelinks (BLOCKS, BLOCKED_BY, RELATES_TO, DUPLICATES) ──────────────
    issuelinks = fields.get("issuelinks") or []
    triples.extend(_issuelink_triples(issue_node, issuelinks, org))

    # ── IN_SPRINT ─────────────────────────────────────────────────────────────
    for sprint_name in (root.get("sprints") or []):
        triples.append(_triple(
            issue_node, "IN_SPRINT", _sprint_node(project, sprint_name),
            f"Sprint: {sprint_name}",
        ))

    # ── HAS_COMPONENT ─────────────────────────────────────────────────────────
    for comp in (fields.get("components") or []):
        comp_name = comp.get("name") if isinstance(comp, dict) else str(comp)
        if comp_name:
            triples.append(_triple(
                issue_node, "HAS_COMPONENT", _component_node(project, comp_name),
                f"Component: {comp_name}",
            ))

    # ── LABELED (labels → Topic) ──────────────────────────────────────────────
    for label in (fields.get("labels") or []):
        if label:
            triples.append(_triple(
                issue_node, "LABELED",
                {"type": "Topic", "name": label, "node_id": node_id_for("Topic", label.lower())},
                f"Label: {label}",
            ))

    # ── FIX_IN_VERSION ────────────────────────────────────────────────────────
    for ver in (fields.get("fixVersions") or []):
        ver_name = ver.get("name") if isinstance(ver, dict) else str(ver)
        if ver_name:
            triples.append(_triple(
                issue_node, "FIX_IN_VERSION", _version_node(project, ver_name),
                f"Fix version: {ver_name}",
            ))

    # ── RESOLVED_BY (from changelog events) ──────────────────────────────────
    resolver = _find_resolver(events)
    if resolver:
        triples.append(_triple(
            issue_node, "RESOLVED_BY", _person_node(resolver),
            f"Resolved by: {resolver.get('displayName', resolver['accountId'])}",
        ))

    # ── Build result ──────────────────────────────────────────────────────────
    status = (fields.get("status") or {}).get("name", "")
    summary = fields.get("summary") or thread.get("subject", "")
    tags = root.get("tags") or []
    dept_tags = _infer_dept_tags(project, tags)
    sensitivity = "confidential" if root.get("extra_scope_ids") else "internal"

    return {
        "thread_id": thread_id,
        "triples": triples,
        "thread_summary": f"{issue_type_name} {issue_key}: {summary} [{status}]",
        "department_tags": dept_tags,
        "sensitivity": sensitivity,
        "_source": "deterministic",
        "email_count": len(events),
        # Pass through scope/citation so the merge step can copy them.
        "scope_id": thread.get("scope_id"),
        "extra_scope_ids": thread.get("extra_scope_ids"),
        "source_ref": thread.get("source_ref"),
        "source_url": thread.get("source_url"),
        "project": project,
        # Surface the issue node so the merge step can attach DERIVED_FROM.
        "_issue_node": issue_node,
    }


def _empty_result(thread_id: str, org: str) -> dict:
    return {
        "thread_id": thread_id,
        "triples": [],
        "thread_summary": "",
        "department_tags": ["general"],
        "sensitivity": "internal",
        "_source": "deterministic_empty",
        "email_count": 0,
    }
