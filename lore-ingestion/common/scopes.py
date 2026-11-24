"""
common/scopes.py
────────────────
Single source of truth for ACL scope and principal identifier formats.

Why this exists:
    Access control depends on string identifiers (`slack:channel:C123`,
    `jira:project:ENG`, `jira:Uabc...`) being byte-identical everywhere
    they are produced and consumed. Before this module the formats were
    inlined as f-strings in normalizers, discovery, grouping, filter, and
    person_registry — a single drift (a stray colon, a renamed prefix)
    silently breaks the query-time gate, which then denies everyone with
    no error. One owner removes that failure mode.

Contract (do not change without a data migration — these strings are
persisted in Neo4j and in `*_scope_principals.json` ACL artifacts):

    Scopes (what a knowledge node is gated by):
        slack:channel:{channel_id}
        slack:workspace:{team_id}
        jira:project:{project_key}
        jira:seclevel:{level_id}
        person:{email}          # Gmail thread participants (OR-bucket)

    Principals (who is granted access):
        slack:{user_id}
        jira:U{account_id}      # NOTE: no colon after U
        jira:G{group_id}        # NOTE: no colon after G
"""

from __future__ import annotations

# ── Prefixes (the one place these literals are defined) ───────────────
JIRA_USER_PREFIX = "jira:U"
JIRA_GROUP_PREFIX = "jira:G"


# ── Scope builders ────────────────────────────────────────────────────
def slack_channel(channel_id: str) -> str:
    return f"slack:channel:{channel_id}"


def slack_workspace(team_id: str) -> str:
    return f"slack:workspace:{team_id}"


def jira_project(project_key: str) -> str:
    return f"jira:project:{project_key}"


def jira_seclevel(level_id: str) -> str:
    return f"jira:seclevel:{level_id}"


def teams_channel(team_id: str, channel_id: str) -> str:
    return f"teams:channel:{team_id}:{channel_id}"


def outlook_thread(conversation_id: str) -> str:
    return f"outlook:thread:{conversation_id}"


def ms365_channel_scope(channel_id: str) -> str:
    return f"ms365:channel:{channel_id}"


def is_ms365_channel(scope_id: str) -> bool:
    return scope_id.startswith("ms365:channel:")


def gmail_user(email: str) -> str:
    return f"gmail:user:{email}"


def zoho_cliq_channel(channel_id: str) -> str:
    return f"zoho:cliq:channel:{channel_id}"


def is_zoho_cliq_channel(scope_id: str) -> bool:
    return scope_id.startswith("zoho:cliq:channel:")


def person_email(email: str) -> str:
    """Gmail participant scope. Format agreed with elysia-rbac: person:{email}."""
    return f"person:{email.lower().strip()}"


# ── Principal builders ────────────────────────────────────────────────
def slack_user(user_id: str) -> str:
    return f"slack:{user_id}"


def jira_user(account_id: str) -> str:
    return f"{JIRA_USER_PREFIX}{account_id}"


def zoho_user(zuid: str) -> str:
    return f"zoho:{zuid}"


def jira_group(group_id: str) -> str:
    """Canonical group principal. Idempotent: an already-prefixed id is
    returned unchanged (Jira sometimes hands back `jira:...` already)."""
    sid = str(group_id)
    return sid if sid.startswith("jira:") else f"{JIRA_GROUP_PREFIX}{sid}"


# ── Parsers (inverse of the builders) ─────────────────────────────────
def strip_jira_user(principal_id: str) -> str:
    """Return the bare Jira accountId from a `jira:U{accountId}` principal."""
    return principal_id.removeprefix(JIRA_USER_PREFIX)


def is_jira_user(principal_id: str) -> bool:
    return principal_id.startswith(JIRA_USER_PREFIX)


__all__ = [
    "JIRA_USER_PREFIX",
    "JIRA_GROUP_PREFIX",
    "slack_channel",
    "slack_workspace",
    "jira_project",
    "jira_seclevel",
    "teams_channel",
    "outlook_thread",
    "slack_user",
    "jira_user",
    "jira_group",
    "strip_jira_user",
    "is_jira_user",
    "ms365_channel_scope",
    "is_ms365_channel",
    "gmail_user",
    "zoho_cliq_channel",
    "is_zoho_cliq_channel",
    "zoho_user",
    "person_email",
]
