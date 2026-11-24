"""
pipeline/identity/source_identity.py
────────────────────────────────────
The modular seam (CENTRALIZED_IDENTITY.md §3).

Every connector is normalized into one uniform `SourceIdentity` record. The resolver
consumes ONLY this struct — it never imports a connector. Adding GitLab/GitHub/Teams later
means writing one `from_<source>()` adapter here and registering its key prefix in
`common/scopes.py`; the resolver, store, and artifact do not change.

Each adapter reads an artifact the pipeline ALREADY produces (no new fetching):
    from_gmail  ← output/knowledge/registry/{org}_person_registry.json   (Workspace directory)
    from_slack  ← output/slack/{org}_scope_principals.json               (Grid admin.users.list)
    from_jira   ← output/jira/{org}_scope_principals.json                (users/search)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from common.email_canon import canonical_email, email_alias_key
from common.logger import get_logger
from common.output_paths import KnowledgePaths, SlackPaths, JiraPaths, OutlookPaths
from common import scopes

log = get_logger("identity.adapters")

# kind taxonomy (CENTRALIZED_IDENTITY.md §2.4). Never merge across the human boundary.
INTERNAL = "internal_person"
EXTERNAL = "external_person"
FUNCTIONAL = "functional"
AUTOMATION = "automation"


@dataclass
class SourceIdentity:
    """One identity as a single source sees it. The whole extension surface."""
    source: str                              # "gmail" | "slack" | "jira" | ...
    native_key: str                          # alias key: "slack:U…", "jira:U…", "email:…" (gmail)
    emails: list[str] = field(default_factory=list)   # raw; canonicalized → email: keys (MAY be empty)
    names: list[str] = field(default_factory=list)    # display/real — Tier-2 candidates, never a key
    kind: str = EXTERNAL
    extra_keys: list[str] = field(default_factory=list)   # e.g. Grid enterprise id; usually []
    is_internal: bool = False                # source hint; directory membership is authoritative
    raw: dict = field(default_factory=dict)

    def alias_keys(self) -> list[str]:
        """Every alias key this identity contributes: native + extras + canonical emails."""
        keys = [self.native_key, *self.extra_keys]
        keys += [email_alias_key(e) for e in self.emails]
        # de-dup preserving order, drop empties
        seen, out = set(), []
        for k in keys:
            if k and k not in seen:
                seen.add(k)
                out.append(k)
        return out


# ── helpers ───────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> dict | None:
    if not path.exists():
        log.info("absent: %s", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("unreadable %s: %s", path, exc)
        return None


def _is_functional(emails: list[str]) -> bool:
    # Imported lazily to avoid a heavy import at module load.
    from pipeline.person_registry import _is_functional_email
    return any(_is_functional_email(e) for e in emails if e)


# ── Gmail ───────────────────────────────────────────────────────────────────
def from_gmail(org: str, data_dir: Path, org_domains: set[str]) -> list[SourceIdentity]:
    """Gmail's native key IS the canonical email — no separate id. Reads the registry."""
    reg = _load_json(KnowledgePaths(org, data_dir).person_registry) or {}
    out: list[SourceIdentity] = []
    for p in reg.get("persons", []):
        if p.get("merged_into"):
            continue
        raw_emails = (p.get("all_emails") or p.get("emails")
                      or ([p["primary_email"]] if p.get("primary_email") else []))
        canon = [c for c in (canonical_email(e) for e in raw_emails) if c]
        if not canon:
            continue
        is_internal = bool(p.get("is_internal")) or any(c.split("@")[1] in org_domains for c in canon)
        if _is_functional(canon) or p.get("is_functional"):
            kind = FUNCTIONAL
        else:
            kind = INTERNAL if is_internal else EXTERNAL
        names = [n for n in ([p.get("canonical_name")] + (p.get("all_names") or [])) if n]
        out.append(SourceIdentity(
            source="gmail",
            native_key=email_alias_key(canon[0]),   # gmail key = primary email
            emails=canon,
            names=names,
            kind=kind,
            is_internal=is_internal,
            raw={"person_id": p.get("person_id")},
        ))
    log.info("from_gmail: %d identities", len(out))
    return out


# ── Slack ───────────────────────────────────────────────────────────────────
def from_slack(org: str, data_dir: Path, org_domains: set[str]) -> list[SourceIdentity]:
    """Slack principals from scope_principals.json. native_key = slack:{user_id}.

    On Grid an enterprise id (W…) would arrive as an extra key (connector TODO); we read it
    defensively if present so this adapter is forward-compatible.
    """
    art = _load_json(SlackPaths(org, data_dir).scope_principals) or {}
    out: list[SourceIdentity] = []
    for pr in art.get("principals", []):
        pid = pr.get("principal_id")
        if not pid or pid in ("slack:unknown", ""):
            continue
        email = pr.get("email") or ""
        canon = [c for c in [canonical_email(email)] if c]
        is_bot = bool(pr.get("is_bot"))
        # external if a foreign-workspace marker is present (connector flags these)
        is_external = bool(pr.get("is_external")) or "#EXT#" in (email or "")
        if is_bot:
            kind = AUTOMATION
        elif is_external:
            kind = EXTERNAL
        elif _is_functional(canon):
            kind = FUNCTIONAL
        else:
            # A principal in the workspace directory is an internal member by presence —
            # email domain is unreliable on Slack (members may use personal addresses).
            kind = INTERNAL
        extra = []
        ent = pr.get("enterprise_user_id")
        if ent:
            extra.append(scopes.slack_user(ent))
        out.append(SourceIdentity(
            source="slack",
            native_key=pid,
            emails=[email] if email else [],
            names=[n for n in [pr.get("display_name")] if n],
            kind=kind,
            extra_keys=extra,
            is_internal=(kind == INTERNAL),
            raw={"principal_id": pid},
        ))
    log.info("from_slack: %d identities", len(out))
    return out


# ── Jira ────────────────────────────────────────────────────────────────────
def from_jira(org: str, data_dir: Path, org_domains: set[str]) -> list[SourceIdentity]:
    """Jira principals. native_key = jira:U{accountId}. emails frequently EMPTY (GDPR)."""
    art = _load_json(JiraPaths(org, data_dir).scope_principals) or {}
    out: list[SourceIdentity] = []
    for pr in art.get("principals", []):
        pid = pr.get("principal_id")
        if not pid or pid.startswith("jira:G") or pid in ("jira:Uunknown", ""):
            continue
        acct_type = (pr.get("account_type") or "atlassian").lower()
        email = pr.get("email") or ""
        canon = [c for c in [canonical_email(email)] if c]
        if acct_type == "app":
            kind = AUTOMATION
        elif acct_type == "customer":
            kind = EXTERNAL
        elif _is_functional(canon):
            kind = FUNCTIONAL
        else:
            is_internal = bool(canon) and canon[0].split("@")[1] in org_domains
            # accountType=atlassian is a weak internal hint when email is hidden;
            # the roster (directory) is authoritative and resolves it at Tier-2a.
            kind = INTERNAL if (is_internal or not canon) else EXTERNAL
        out.append(SourceIdentity(
            source="jira",
            native_key=pid,
            emails=[email] if email else [],
            names=[n for n in [pr.get("display_name")] if n],
            kind=kind,
            is_internal=(kind == INTERNAL),
            raw={"principal_id": pid, "account_type": acct_type},
        ))
    log.info("from_jira: %d identities", len(out))
    return out


# ── MS365 (Outlook Mail + Teams) ────────────────────────────────────────────
def from_ms365(org: str, data_dir: Path, org_domains: set[str]) -> list[SourceIdentity]:
    """MS365 principals from the standard scope_principals.json.

    native_key = ms365:{objectId}  (Azure AD object ID)
    email comes from the identities artifact (may be UPN when mail is absent).
    Teams channel memberships arrive via scope_principals — no extra join needed.
    """
    art = _load_json(OutlookPaths(org, data_dir).scope_principals) or {}
    out: list[SourceIdentity] = []
    for pr in art.get("principals", []):
        pid = pr.get("principal_id") or ""
        if not pid or not pid.startswith("ms365:"):
            continue
        email = pr.get("email") or ""
        canon = [c for c in [canonical_email(email)] if c]
        acct_type = (pr.get("account_type") or "member").lower()
        if acct_type not in ("member", "guest"):
            kind = AUTOMATION
        elif acct_type == "guest":
            kind = EXTERNAL
        elif _is_functional(canon):
            kind = FUNCTIONAL
        else:
            is_internal = bool(canon) and canon[0].split("@")[1] in org_domains
            kind = INTERNAL if (is_internal or not canon) else EXTERNAL
        out.append(SourceIdentity(
            source="ms365",
            native_key=pid,
            emails=[email] if email else [],
            names=[n for n in [pr.get("display_name")] if n],
            kind=kind,
            is_internal=(kind == INTERNAL),
            raw={"principal_id": pid, "account_type": acct_type},
        ))
    log.info("from_ms365: %d identities", len(out))
    return out


# Registry of adapters — the ONLY place a new source is added.
ADAPTERS = {
    "gmail": from_gmail,
    "slack": from_slack,
    "jira": from_jira,
    "ms365": from_ms365,
}
