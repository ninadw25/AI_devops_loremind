"""
pipeline/jira/identity.py
──────────────────────────
P5 — Identity emission for the central resolver + pre-computed person→scope map.

Two artifacts are produced:

1. {org}_jira_identities.json — who exists in Jira
   { accountId, display_name, email?, account_type, source }
   Sourced from scope_principals.json (ACL-known users) unioned with event
   actors (comment/changelog authors who touched issues but may lack a grant).

2. {org}_jira_person_scopes.json — what each human can access
   { accountId → [scope_id, ...] }
   Pre-flattened from scope_principals.json: group grants are expanded through
   group_members, app/bot accounts are excluded. The Bun.js backend does a
   single lookup by accountId and forwards the scope list to lore-engine,
   which uses it as the RBAC filter on Neo4j queries. No graph traversal needed
   at query time.

Design: JIRA_PERSON_ID_RESOLUTION.md §7, JIRA_ACL_FOR_RBAC.md §9.
"""

from __future__ import annotations

import json
from pathlib import Path

from common.logger import get_logger
from common.output_paths import JiraPaths

log = get_logger("pipeline.jira.identity")


def collect_identities(
    org: str,
    data_dir: Path,
    raw_path: Path | None = None,
) -> list[dict]:
    """Collect Jira identity facts from ACL principals + event actors.

    Args:
        org:        Org slug (e.g. "loremind").
        data_dir:   Root output directory (output/).
        raw_path:   Path to the raw Jira JSONL event file. When provided,
                    event actors are unioned with ACL principals. Optional —
                    scope_principals.json alone is sufficient for ACL users.

    Returns:
        List of SourceIdentity dicts:
          { accountId, display_name, email?, account_type, source }
    """
    paths = JiraPaths(org=org, data_dir=data_dir)
    seen: dict[str, dict] = {}  # accountId → identity dict

    # ── Source 1: scope_principals.json ──────────────────────────────────────
    if paths.scope_principals.exists():
        try:
            artifact = json.loads(paths.scope_principals.read_text(encoding="utf-8"))
            for p in artifact.get("principals", []) or []:
                acct_id = _extract_account_id(p.get("principal_id") or "")
                if not acct_id:
                    continue
                seen[acct_id] = {
                    "accountId": acct_id,
                    "display_name": p.get("display_name") or p.get("displayName") or acct_id,
                    "email": (p.get("email") or "").lower() or None,
                    "account_type": p.get("account_type") or p.get("accountType") or "atlassian",
                    "source": "acl_principals",
                }
            log.info("Identity: %d principals from scope_principals.json", len(seen))
        except Exception as exc:
            log.warning("Could not read scope_principals.json: %s", exc)
    else:
        log.warning("scope_principals.json not found at %s", paths.scope_principals)

    # ── Source 2: raw event actors ────────────────────────────────────────────
    if raw_path and raw_path.exists():
        actor_count = 0
        with open(raw_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for actor in event.get("actors") or []:
                    acct_id = actor.get("accountId") or ""
                    if not acct_id or acct_id in seen:
                        continue
                    seen[acct_id] = {
                        "accountId": acct_id,
                        "display_name": actor.get("displayName") or acct_id,
                        "email": (actor.get("emailAddress") or "").lower() or None,
                        "account_type": actor.get("accountType") or "atlassian",
                        "source": "event_actor",
                    }
                    actor_count += 1
        log.info("Identity: +%d actors from raw events (total: %d)", actor_count, len(seen))

    return list(seen.values())


def emit_identities(
    org: str,
    data_dir: Path,
    raw_path: Path | None = None,
) -> Path:
    """Collect and write Jira identities to {org}_jira_identities.json.

    Returns the path of the written file.
    """
    identities = collect_identities(org=org, data_dir=data_dir, raw_path=raw_path)
    out_dir = data_dir / "jira"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_jira_identities.json"

    payload = {
        "org": org,
        "source": "jira",
        "identities": identities,
        "count": len(identities),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Identity: wrote %d Jira identities → %s", len(identities), out_path)
    return out_path


def build_person_scope_map(artifact: dict) -> dict[str, list[str]]:
    """Expand the raw ACL artifact into a flat accountId → [scope_ids] map.

    Reads principals, scope_principals (grant edges), groups, and group_members
    from scope_principals.json and produces a pre-computed mapping that the
    backend can use directly — no graph traversal at query time.

    App/bot accounts (account_type == "app") are excluded: they are automation
    integrations, not human users.

    Security levels (jira:seclevel:*) are included in the scope list — the
    backend passes the full list and lore-engine applies the AND-semantics
    for scoped issues (scope_all_ids) automatically.
    """
    # Index principals by principal_id for type lookup
    principal_type: dict[str, str] = {}
    for p in artifact.get("principals", []) or []:
        pid = p.get("principal_id", "")
        if pid:
            principal_type[pid] = p.get("account_type", "atlassian")

    # Expand group_members: group_principal_id → set of member principal_ids
    group_members: dict[str, set[str]] = {}
    for gm in artifact.get("group_members", []) or []:
        gid = gm.get("group_id", "")
        mid = gm.get("principal_id", "")
        if gid and mid:
            group_members.setdefault(gid, set()).add(mid)

    # Walk grant edges and build accountId → scope set
    person_scopes: dict[str, set[str]] = {}

    for sp in artifact.get("scope_principals", []) or []:
        scope_id = sp.get("scope_id", "")
        pid = sp.get("principal_id", "")
        ptype = sp.get("principal_type", "")
        if not scope_id or not pid:
            continue

        if ptype == "user":
            acct_id = _extract_account_id(pid)
            if not acct_id:
                continue
            # Skip app/bot accounts
            if principal_type.get(pid, "atlassian") == "app":
                continue
            person_scopes.setdefault(acct_id, set()).add(scope_id)

        elif ptype == "group":
            # Expand group → its members
            for member_pid in group_members.get(pid, set()):
                acct_id = _extract_account_id(member_pid)
                if not acct_id:
                    continue
                if principal_type.get(member_pid, "atlassian") == "app":
                    continue
                person_scopes.setdefault(acct_id, set()).add(scope_id)

    return {acct_id: sorted(scopes) for acct_id, scopes in person_scopes.items()}


def emit_person_scope_map(org: str, data_dir: Path) -> Path:
    """Build and write the flat accountId → scopes map to {org}_jira_person_scopes.json.

    Returns the path of the written file.
    """
    paths = JiraPaths(org=org, data_dir=data_dir)

    if not paths.scope_principals.exists():
        log.warning("scope_principals.json not found at %s — skipping scope map", paths.scope_principals)
        return paths.scope_principals  # caller can check .exists()

    try:
        artifact = json.loads(paths.scope_principals.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read scope_principals.json: %s", exc)
        return paths.scope_principals

    scope_map = build_person_scope_map(artifact)

    out_dir = data_dir / "jira"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_jira_person_scopes.json"

    payload = {
        "org": org,
        "source": "jira",
        "person_count": len(scope_map),
        "scopes": scope_map,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(
        "Scope map: wrote %d human accounts → %s",
        len(scope_map), out_path,
    )
    return out_path


def _extract_account_id(principal_id: str) -> str:
    """Strip the jira:U prefix if present, return the raw accountId."""
    if not principal_id:
        return ""
    # Formats: "jira:U<accountId>" or plain accountId
    if principal_id.startswith("jira:U"):
        return principal_id[6:]
    # Other prefixes (group, app) are not account-level
    if ":" in principal_id:
        return ""
    return principal_id
