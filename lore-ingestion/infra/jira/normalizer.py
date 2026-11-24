"""Normalize Jira issue, comment, and changelog JSON into KnowledgeEvent dicts."""
from __future__ import annotations

from typing import Any, Dict, List

from common import scopes
from common.hashing import make_id

SENTINEL_CODE_END = object()

_CHANGELOG_EVENT_TYPES: Dict[str, str] = {
    "status": "jira.issue.transitioned",
    "priority": "jira.issue.transitioned",
    "resolution": "jira.issue.resolved",
    "assignee": "jira.issue.reassigned",
}


def _build_actors(fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return reporter then assignee, deduped by accountId."""
    seen: set[str] = set()
    actors: List[Dict[str, Any]] = []
    for candidate in (fields.get("reporter"), fields.get("assignee")):
        if not candidate:
            continue
        acct = candidate.get("accountId")
        if acct and acct not in seen:
            seen.add(acct)
            actors.append(candidate)
    return actors


def adf_to_text(node: Any) -> str:
    if not node:
        return ""

    output: List[str] = []
    stack = [node]

    while stack:
        cur = stack.pop()
        if cur is SENTINEL_CODE_END:
            output.append("\n```")
            continue

        typ = cur.get("type") if isinstance(cur, dict) else None
        if typ == "text":
            output.append(cur.get("text", ""))
            continue
        if typ == "hardBreak":
            output.append("\n")
            continue
        if typ == "codeBlock":
            output.append("\n```")
            stack.append(SENTINEL_CODE_END)
            for child in reversed(cur.get("content", [])):
                stack.append(child)
            continue

        for child in reversed(cur.get("content", []) if isinstance(cur, dict) else []):
            stack.append(child)

    return "".join(output).strip()


def truncate_body(description: str, comments: List[str]) -> str:
    comments = [c[:2000] if len(c) > 2000 else c for c in comments]
    n = len(comments)
    if n == 0:
        return description[:3000]
    if n <= 10:
        return (description[:1500] + "\n\n" + "\n\n".join(comments))[:8000]
    return (description[:1000] + "\n\n" + "\n\n".join(comments[:3]) + f"\n\n[{n-6} middle comments omitted]\n\n" + "\n\n".join(comments[-3:]))[:8000]


def issue_type_to_event_type(name: str) -> str:
    mapping = {
        "Epic": "jira.epic.created",
        "Story": "jira.story.created",
        "Bug": "jira.bug.created",
        "Sub-task": "jira.subtask.created",
    }
    return mapping.get(name, "jira.issue.created")


def normalize_comment(comment: Dict[str, Any], issue: Dict[str, Any], org: str, base_url: str | None = None) -> Dict[str, Any] | None:
    body = adf_to_text(comment.get("body", {})).strip()
    if not body or len(body) <= 1:
        return None

    author = comment.get("author") or {}
    primary = author.get("accountId") or "unknown"
    issue_key = issue.get("key") or issue.get("id") or "unknown"

    raw = dict(comment)
    if comment.get("visibility") is not None:
        raw["comment_visibility"] = comment.get("visibility")

    fields = issue.get("fields", {})
    project_key = (fields.get("project") or {}).get("key", "")
    security = fields.get("security")
    extra_scope_ids = [scopes.jira_seclevel(security.get('id'))] if security else []

    event = {
        "id": make_id("jira", f"{issue_key}#comment-{comment.get('id')}"),
        "source": "jira",
        "source_id": f"{issue_key}#comment-{comment.get('id')}",
        "event_type": "jira.comment.added",
        "timestamp": comment.get("created"),
        "title": f"Re: {fields.get('summary', '')}",
        "body": body[:2000],
        "primary_actor": primary,
        "actors": [author] if author else [],
        "parent_id": issue_key,
        "org": org,
        "scope_id": scopes.jira_project(project_key) if project_key else None,
        "extra_scope_ids": extra_scope_ids,
        "raw": raw,
    }
    if base_url:
        event["source_url"] = f"{base_url.rstrip('/')}/browse/{issue_key}"
        event["source_ref"] = issue_key
    return event


def normalize_changelog(history: Dict[str, Any], issue: Dict[str, Any], org: str, base_url: str | None = None) -> Dict[str, Any] | None:
    items = history.get("items", [])
    significant_fields = {"status", "assignee", "resolution", "priority"}
    significant = [it for it in items if it.get("field") in significant_fields]
    if not significant:
        return None

    it = significant[0]
    issue_key = issue.get("key") or issue.get("id") or "unknown"
    raw = dict(history)
    fields = issue.get("fields", {})
    project_key = (fields.get("project") or {}).get("key", "")
    security = fields.get("security")
    extra_scope_ids = [scopes.jira_seclevel(security.get('id'))] if security else []

    event = {
        "id": make_id("jira", f"{issue_key}#change-{history.get('id')}"),
        "source": "jira",
        "source_id": f"{issue_key}#change-{history.get('id')}",
        "event_type": _CHANGELOG_EVENT_TYPES.get(it.get("field", ""), "jira.issue.transitioned"),
        "timestamp": history.get("created"),
        "title": f"{it.get('field').title()}: {it.get('fromString')} → {it.get('toString')}",
        "body": str(it),
        "primary_actor": history.get("author", {}).get("accountId"),
        "parent_id": issue_key,
        "org": org,
        "scope_id": scopes.jira_project(project_key) if project_key else None,
        "extra_scope_ids": extra_scope_ids,
        "raw": raw,
    }
    if base_url:
        event["source_url"] = f"{base_url.rstrip('/')}/browse/{issue_key}"
        event["source_ref"] = issue_key
    return event


def _extract_epic_link(fields: Dict[str, Any], field_map: Dict[str, str] | None) -> List[str]:
    """Return list of Epic / parent epic keys linked to this issue.

    Tries (in order):
      1. fields.parent.key          — Jira Next-gen / sub-issues
      2. field_map["Epic Link"]     — classic board epic link custom field
      3. customfield_10014          — common default for Epic Link
    """
    related: List[str] = []
    # Next-gen: parent is a first-class field
    parent_key = (fields.get("parent") or {}).get("key")
    if parent_key:
        related.append(parent_key)
    # Classic board: epic link via discovered field map
    epic_field_id = (field_map or {}).get("Epic Link") or "customfield_10014"
    epic_key = fields.get(epic_field_id)
    if isinstance(epic_key, str) and epic_key and epic_key not in related:
        related.append(epic_key)
    elif isinstance(epic_key, dict):
        k = epic_key.get("key") or epic_key.get("id")
        if k and k not in related:
            related.append(k)
    return related


def normalize_issue(issue: Dict[str, Any], org: str, base_url: str | None = None, field_map: Dict[str, str] | None = None) -> List[Dict[str, Any]]:
    fields = issue.get("fields", {})
    summary = fields.get("summary")
    if not summary:
        return []

    description = adf_to_text(fields.get("description") or {})
    comments = [adf_to_text(c.get("body", {})) for c in fields.get("comment", {}).get("comments", [])]
    body = truncate_body(description, comments)
    project_key = (fields.get("project") or {}).get("key") or ""
    security = fields.get("security") or {}
    extra_scope_ids: List[str] = []
    if security.get("id"):
        extra_scope_ids.append(scopes.jira_seclevel(security.get('id')))

    event = {
        "id": make_id("jira", f"{org}:{issue.get('key')}") ,
        "source": "jira",
        "source_id": issue.get("key"),
        "event_type": issue_type_to_event_type((fields.get("issuetype") or {}).get("name", "")),
        "timestamp": fields.get("created"),
        "title": summary,
        "body": body,
        "primary_actor": (fields.get("reporter") or {}).get("accountId"),
        "actors": _build_actors(fields),
        "project": project_key,
        "tags": (fields.get("labels") or []) + [c.get("name") for c in (fields.get("components") or []) if c.get("name")],
        "parent_id": (fields.get("parent") or {}).get("key"),
        "related_ids": _extract_epic_link(fields, field_map),
        "org": org,
        "raw": issue,
        "scope_id": scopes.jira_project(project_key) if project_key else None,
        "extra_scope_ids": extra_scope_ids,
    }

    if base_url:
        event["source_ref"] = issue.get("key")
        event["source_url"] = f"{base_url.rstrip('/')}/browse/{issue.get('key')}"
        event["url"] = event["source_url"]

    sprints = []
    sprint_field_id = (field_map or {}).get("Sprint") or "customfield_10020"
    cf_sprints = fields.get(sprint_field_id) or []
    for s in cf_sprints:
        name = None
        if isinstance(s, dict):
            name = s.get("name") or s.get("value")
        elif isinstance(s, str):
            name = s
        if name:
            sprints.append(name)
    if sprints:
        event["sprints"] = sprints

    attachments = []
    for a in (fields.get("attachment") or []):
        attachments.append({
            "filename": a.get("filename"),
            "mimeType": a.get("mimeType") or a.get("contentType"),
            "url": a.get("content") or a.get("self"),
            "uploader": (a.get("author") or {}).get("accountId") or (a.get("author") or {}).get("displayName"),
            "timestamp": a.get("created"),
        })
    if attachments:
        try:
            event_raw = event.get("raw") or {}
            event_raw["attachments_meta"] = attachments
            event["raw"] = event_raw
        except Exception:
            pass

    child_events: List[Dict[str, Any]] = []
    for c in fields.get("comment", {}).get("comments", []):
        ce = normalize_comment(c, issue, org, base_url=base_url)
        if ce:
            child_events.append(ce)

    for h in issue.get("changelog", {}).get("histories", []):
        ch = normalize_changelog(h, issue, org, base_url=base_url)
        if ch:
            child_events.append(ch)

    return [event] + child_events
