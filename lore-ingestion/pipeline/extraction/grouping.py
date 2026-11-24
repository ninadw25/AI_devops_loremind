"""
pipeline/extraction/grouping.py
───────────────────────────────
Thread grouping and person-registry loading for extraction.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from common import scopes
from common.logger import get_logger
from common.sources import caps_for

log = get_logger("pipeline.triple_extractor")


# ── W1: Slack channel + time-window sessionization ──────────────────────────
def _raw(event: dict) -> dict:
    return event.get("raw", {}) or {}


def _slack_ts(event: dict) -> float:
    try:
        return float(_raw(event).get("ts") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _slack_ts_str(event: dict) -> str:
    return str(_raw(event).get("ts") or "")


def _slack_channel(event: dict) -> str:
    """Channel id for a Slack event, from slack_meta → scope_id → source_ref."""
    raw = _raw(event)
    cid = (raw.get("slack_meta", {}) or {}).get("channel_id")
    if cid:
        return cid
    scope = raw.get("scope_id", "") or ""
    if scope.startswith("slack:channel:"):
        return scope.split(":", 2)[2]
    ref = raw.get("source_ref", "") or ""
    return ref.split(":", 1)[0] if ref else ""


def _sessionize_channel(channel_id: str, events: list[dict],
                        idle_gap_s: float, max_messages: int) -> list[dict]:
    """Group ONE channel's events into conversation sessions.

    Roots (top-level messages) are split by idle gap and a max-size cap; each
    reply folds into the session holding its root, so a reply-tree never splits
    across sessions. A reply whose root isn't in the window acts as its own root.
    Returns fully-shaped thread dicts (thread_id = slack:session:{chan}:{first_ts}).
    """
    # A message is a reply iff thread_ts is set and differs from its own ts AND
    # its root is actually present in this window; an orphan reply acts as a root.
    root_ts = {_slack_ts_str(e) for e in events
               if not (_raw(e).get("thread_ts") and _raw(e).get("thread_ts") != _slack_ts_str(e))}

    def _is_reply(e: dict) -> bool:
        tts = _raw(e).get("thread_ts") or ""
        return bool(tts) and tts != _slack_ts_str(e) and tts in root_ts

    roots = sorted((e for e in events if not _is_reply(e)), key=_slack_ts)
    replies = [e for e in events if _is_reply(e)]

    sessions: list[dict] = []
    cur: dict | None = None
    for r in roots:
        ts = _slack_ts(r)
        if (cur is None
                or ts - cur["_last_ts"] > idle_gap_s
                or len(cur["emails"]) >= max_messages):
            cur = {"emails": [], "_root_ts": set(), "_last_ts": ts,
                   "_first_ts_str": _slack_ts_str(r)}
            sessions.append(cur)
        cur["emails"].append(r)
        cur["_root_ts"].add(_slack_ts_str(r))
        cur["_last_ts"] = ts

    # Fold replies into the session that owns their root.
    root_to_session = {rts: s for s in sessions for rts in s["_root_ts"]}
    for rep in replies:
        s = root_to_session.get(_raw(rep).get("thread_ts", ""))
        if s is not None:
            s["emails"].append(rep)

    out: list[dict] = []
    for s in sessions:
        evs = sorted(s["emails"], key=_slack_ts)
        first = evs[0]
        sid = f"slack:session:{channel_id}:{s['_first_ts_str']}"
        thread = {
            "emails": evs,
            "subject": first.get("title", "") or "",
            "thread_id": sid,
        }
        raw0 = _raw(first)
        if raw0.get("scope_id"):
            thread["scope_id"] = raw0.get("scope_id")
        thread["source_ref"] = raw0.get("source_ref")
        thread["source_url"] = raw0.get("source_url")
        out.append(thread)
    return out


def group_by_thread(filtered_path: Path) -> dict:
    """Read filtered JSONL and group records by source-specific thread key.

    Gmail keeps the legacy thread_id/parent_id/source_id fallback path.
    Slack groups by raw.thread_ts (falling back to ts/source_id when absent).

    Returns dict[thread_id|thread_ts] → {"emails": [...], "subject": str, ...}
    sorted by timestamp within each thread.

    For Jira events the root issue (parent_id is None) carries scope_id and
    extra_scope_ids; these are hoisted onto the thread dict so the builder
    can stamp scope_ids / scope_all_ids on every knowledge node extracted from
    the thread.
    """
    threads: dict[str, dict] = {}
    # Session-strategy sources (Slack) are bucketed by channel, then sessionized
    # after the full pass — a session spans many events, not one line.
    session_buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    session_caps: dict[str, object] = {}

    with open(filtered_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)

            raw = event.get("raw", {}) or {}
            source = event.get("source", "")
            caps = caps_for(source)

            if caps.group_strategy == "session":
                session_buckets[(source, _slack_channel(event))].append(event)
                session_caps[source] = caps
                continue

            # Thread key: source-specific raw field(s) → parent_id → source_id.
            tid = None
            for _fld in caps.thread_key_raw_fields:
                tid = raw.get(_fld)
                if tid:
                    break
            tid = tid or event.get("parent_id") or event.get("source_id", event["id"])

            if tid not in threads:
                threads[tid] = {
                    "emails": [],
                    "subject": event.get("title", ""),
                    "thread_id": tid,
                }
                # Sources that carry ACL scope + citation inside `raw` (Slack).
                if caps.scope_in_raw:
                    if raw.get("scope_id"):
                        threads[tid]["scope_id"] = raw.get("scope_id")
                    threads[tid]["source_ref"] = raw.get("source_ref")
                    threads[tid]["source_url"] = raw.get("source_url")
            threads[tid]["emails"].append(event)

            # Hoist Jira ACL and citation fields from the root issue event.
            # Comments reference the issue via parent_id so they never carry
            # scope_id themselves — we take it from the first event that has it.
            if event.get("scope_id") and not threads[tid].get("scope_id"):
                threads[tid]["scope_id"] = event["scope_id"]
                threads[tid]["extra_scope_ids"] = event.get("extra_scope_ids", [])
                for _k in ("source_ref", "source_url", "project"):
                    if event.get(_k):
                        threads[tid][_k] = event[_k]

    # Expand session-strategy buckets (Slack) into per-session threads.
    for (source, channel_id), evs in session_buckets.items():
        caps = session_caps[source]
        idle_gap_s = caps.session_idle_gap_minutes * 60
        sessions = _sessionize_channel(
            channel_id, evs, idle_gap_s, caps.session_max_messages)
        for sess in sessions:
            threads[sess["thread_id"]] = sess
    if session_buckets:
        log.info("Sessionized %d Slack channel(s) → %d sessions from %d events",
                 len(session_buckets),
                 sum(1 for t in threads.values()
                     if str(t.get("thread_id", "")).startswith("slack:session:")),
                 sum(len(evs) for evs in session_buckets.values()))

    # Union Gmail scope_ids across all messages in each thread.
    # Each message carries raw["scope_ids"] stamped at normalize time;
    # here we union them so the thread holds every participant across
    # the full conversation.
    for thread in threads.values():
        if thread.get("scope_id") or thread.get("scope_ids"):
            continue
        emails = thread.get("emails", [])
        if not emails or emails[0].get("source") != "gmail":
            continue
        unioned: set[str] = set()
        for event in emails:
            for sid in (event.get("raw") or {}).get("scope_ids", []):
                if sid:
                    unioned.add(sid)
        if unioned:
            participant_scopes = sorted(unioned)
            thread["scope_ids"] = participant_scopes
            thread["scope_id"] = participant_scopes[0]
            thread["source_ref"] = (emails[0].get("raw") or {}).get("source_ref", "")
            thread["source_url"] = (emails[0].get("raw") or {}).get("source_url", "")

    # Sort emails within each thread by timestamp
    for thread in threads.values():
        thread["emails"].sort(key=lambda e: e.get("timestamp", ""))
        thread["email_count"] = len(thread["emails"])

    return threads


def load_person_registry(org: str, data_dir: Path) -> tuple[dict, dict]:
    """Load person registry.

    Returns (email_to_person_id, persons_by_id).
    """
    # Prefer centralized identity (resolved_registry.json); fall back to the
    # Gmail-built person_registry when no identity run exists.
    from pipeline.identity.registry_view import registry_source
    registry_path = registry_source(org, data_dir)

    if not registry_path.exists():
        log.warning("Person registry not found: %s", registry_path)
        return {}, {}
    log.info("Registry source: %s", registry_path.name)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    email_to_person = registry.get("email_to_person_id", {})
    persons_by_id = {p["person_id"]: p for p in registry.get("persons", [])}

    log.info("Loaded person registry: %d persons, %d email mappings",
             len(persons_by_id), len(email_to_person))

    # Try to augment registry mappings with Jira discovery principals
    try:
        from common.output_paths import JiraPaths
        jira_artifact = JiraPaths(org=org, data_dir=data_dir).scope_principals
        if jira_artifact.exists():
            try:
                art = json.loads(jira_artifact.read_text(encoding="utf-8"))
                principals = art.get("principals", []) or []
                added = 0
                fallback_added = 0
                for p in principals:
                    pid = p.get("principal_id") or p.get("principalId") or ""
                    email = (p.get("email") or "").lower().strip()
                    display_name = (p.get("display_name") or p.get("displayName") or "").strip()
                    if not pid or not email:
                        if not pid:
                            continue
                        fallback_person_id = f"person:{pid}"
                        if fallback_person_id not in persons_by_id:
                            canonical_name = display_name or scopes.strip_jira_user(pid) or pid
                            fallback_person = {
                                "person_id": fallback_person_id,
                                "canonical_name": canonical_name,
                                "primary_email": "",
                                "all_emails": [],
                                "all_names": [display_name] if display_name else [],
                                "is_internal": True,
                                "is_functional": False,
                                "domains": [],
                                "stats": {
                                    "emails_sent": 0,
                                    "emails_received": 0,
                                    "total_activity": 0,
                                    "threads_participated": 0,
                                    "unique_contacts": 0,
                                },
                                "principal_id": pid,
                                "source": "jira_discovery_fallback",
                            }
                            persons_by_id[fallback_person_id] = fallback_person
                            fallback_added += 1
                        email_to_person[pid] = fallback_person_id
                        if scopes.is_jira_user(pid):
                            acct = scopes.strip_jira_user(pid)
                            if acct:
                                email_to_person.setdefault(acct, fallback_person_id)
                        continue
                    # Resolve email to existing person_id
                    person_id = email_to_person.get(email)
                    if not person_id:
                        continue
                    # Map both the full principal id and the account id (strip jira:U prefix)
                    email_to_person.setdefault(pid, person_id)
                    if scopes.is_jira_user(pid):
                        acct = scopes.strip_jira_user(pid)
                        email_to_person.setdefault(acct, person_id)
                    added += 1
                if added:
                    log.info("Augmented registry with %d Jira principal mappings", added)
                if fallback_added:
                    log.info("Created %d Jira fallback people for principals without emails", fallback_added)
            except Exception as exc:
                # A present-but-unreadable artifact means Jira persons silently
                # won't be registered (validation may then reject them) — surface it.
                log.warning("Could not parse Jira discovery artifact %s: %s", jira_artifact, exc)
    except Exception as exc:
        # Optional augmentation — never break extraction over it, but don't
        # swallow it silently either.
        log.warning("Skipped Jira registry augmentation: %s", exc)

    return email_to_person, persons_by_id
