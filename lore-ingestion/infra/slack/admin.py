"""
infra/slack/admin.py
────────────────────
Enterprise Grid admin pathway — the PRIMARY path (~90% of clients).

When the token is an org-installed admin/user token on Enterprise Grid (see
infra/slack/capabilities.py → tier == GRID_ADMIN), we can enumerate channels,
users, roles, and usergroups ORG-WIDE and DETERMINISTICALLY, without the bot
joining channels and without heuristics:

    admin.conversations.search      → every channel in the org (no join needed)
    admin.conversations.getConversationPrefs → who can post / posting prefs
    admin.users.list                → full user directory (email, admin/owner)
    admin.roles.listAssignments     → role assignments (admin/owner/custom)
    usergroups.list (include_users) → usergroup membership → ACL principals
    admin.teams.list                → workspaces in the org

This replaces the bot-fallback discovery (conversations.members, which needs the
bot to be a member and only sees private channels it's in).

KNOWN GAP (confirmed against Slack docs): there is no org-wide admin method that
returns a private channel's full MEMBER list. `admin.conversations.search` lists
the channels; `getConversationPrefs` gives posting prefs (not full visibility);
`conversations.members` is visibility-filtered. The complete member set for
private-channel ACL comes from the Discovery API (separate approved app) — see
[[slack_grid_primary_strategy]]. Until Discovery is wired, private-channel
principals on Grid are best-effort (prefs + usergroup expansion) and flagged.

Testability: the client takes an injectable `requester(method, params) -> dict`
so the whole admin surface is unit-tested with canned responses — no Grid org
required. The default requester is a real HTTP getter with retry/backoff.

Response-shape note: exact field names for admin.users.list /
admin.roles.listAssignments could not be fully rendered from live docs during
research; parsing here is DEFENSIVE (`.get` with fallbacks) and the few assumed
fields are marked `# ASSUMED` so they can be confirmed against a real token.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

import requests

from common import scopes as scope_ids
from common.logger import get_logger

log = get_logger("slack.admin")

SLACK_BASE_URL = "https://slack.com"

Requester = Callable[[str, dict], dict]


class SlackAdminError(RuntimeError):
    """A Grid admin API call failed in a non-recoverable way."""


class SlackAdminClient:
    """Thin client over Slack's admin.* methods with cursor pagination.

    Pass `requester` in tests to return canned `dict` responses keyed off
    (method, params); production uses the real HTTP getter.
    """

    def __init__(
        self,
        token: str,
        base_url: str = SLACK_BASE_URL,
        requester: Optional[Requester] = None,
        request_delay: float = 0.4,
        max_retries: int = 5,
        default_retry_sleep: float = 3.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._requester = requester or self._http_get
        self._request_delay = request_delay
        self._max_retries = max_retries
        self._default_retry_sleep = default_retry_sleep
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    # ── transport ────────────────────────────────────────────────────
    def _http_get(self, method: str, params: dict) -> dict:
        """Real HTTP GET against slack.com/api/<method> with 429/5xx retry.

        admin.* methods sit on lower rate tiers than read methods, so we honor
        Retry-After and back off conservatively.
        """
        url = f"{self.base_url}/api/{method}"
        time.sleep(self._request_delay)
        for attempt in range(1, self._max_retries + 2):
            resp = self._session.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after else self._default_retry_sleep
                if attempt > self._max_retries:
                    raise SlackAdminError(f"{method}: rate limited after {self._max_retries} retries")
                log.warning("[%s] 429 — sleeping %ss (attempt %d)", method, sleep_s, attempt)
                time.sleep(sleep_s)
                continue
            if resp.status_code >= 500:
                if attempt > self._max_retries:
                    resp.raise_for_status()
                time.sleep(min(self._default_retry_sleep * 2 ** (attempt - 1), 60.0))
                continue
            resp.raise_for_status()
            return resp.json()
        raise SlackAdminError(f"{method}: exhausted retries")

    def _get(self, method: str, params: Optional[dict] = None) -> dict:
        data = self._requester(method, params or {})
        if not data.get("ok"):
            err = data.get("error", "unknown_error")
            raise SlackAdminError(f"{method}: {err}")
        return data

    def _paginate(self, method: str, result_key: str, params: Optional[dict] = None) -> Iterable[dict]:
        """Yield items from a cursor-paginated admin method."""
        cursor = ""
        base = dict(params or {})
        base.setdefault("limit", 200)
        while True:
            call = dict(base)
            if cursor:
                call["cursor"] = cursor
            data = self._get(method, call)
            for item in data.get(result_key, []) or []:
                yield item
            cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
            if not cursor:
                break

    # ── admin methods ────────────────────────────────────────────────
    def search_channels(self, query: str = "", team_ids: Optional[list[str]] = None) -> list[dict]:
        """All channels org-wide (admin.conversations.search). No join needed."""
        params: dict = {"search_channel_types": "public,private"}
        if query:
            params["query"] = query
        if team_ids:
            params["team_ids"] = ",".join(team_ids)
        return list(self._paginate("admin.conversations.search", "conversations", params))

    def get_conversation_prefs(self, channel_id: str) -> dict:
        """Posting/visibility prefs for one channel (who_can_post, etc.)."""
        data = self._get("admin.conversations.getConversationPrefs", {"channel_id": channel_id})
        return data.get("prefs", {}) or {}

    def list_users(self, team_id: Optional[str] = None) -> list[dict]:
        """Full org user directory (admin.users.list)."""
        params: dict = {}
        if team_id:
            params["team_id"] = team_id
        return list(self._paginate("admin.users.list", "users", params))

    def list_role_assignments(self, role_ids: Optional[list[str]] = None) -> list[dict]:
        """Role assignments across the org (admin.roles.listAssignments)."""
        params: dict = {}
        if role_ids:
            params["role_ids"] = ",".join(role_ids)
        return list(self._paginate("admin.roles.listAssignments", "role_assignments", params))

    def list_usergroups(self, include_users: bool = True) -> list[dict]:
        """Usergroups with members (usergroups.list include_users=true)."""
        data = self._get("usergroups.list", {
            "include_users": "true" if include_users else "false",
            "include_disabled": "false",
        })
        return data.get("usergroups", []) or []

    def list_teams(self) -> list[dict]:
        """Workspaces in the org (admin.teams.list)."""
        return list(self._paginate("admin.teams.list", "teams"))


# ── normalization into the same shapes discovery.py produces ─────────

@dataclass
class GridDirectory:
    """Deterministic identity + role data assembled from admin methods."""
    principals: list[dict] = field(default_factory=list)   # {principal_id,email,display_name,...}
    roles_by_user: dict[str, list[str]] = field(default_factory=dict)
    usergroup_members: dict[str, list[str]] = field(default_factory=dict)  # group_id -> [user_id]


def _user_record(user: dict) -> dict:
    """Normalize an admin.users.list entry into a principal record.

    DEFENSIVE: admin.users.list field names are partially ASSUMED (see module
    docstring). We try several keys for email/name and fall back gracefully.
    """
    uid = user.get("id") or user.get("user_id") or ""
    profile = user.get("profile", {}) or {}
    email = user.get("email") or profile.get("email") or None  # ASSUMED top-level email
    display = (
        profile.get("display_name")
        or profile.get("real_name")
        or user.get("real_name")
        or user.get("name")
        or uid
    )
    return {
        "principal_id": scope_ids.slack_user(uid),
        "user_id": uid,
        "email": email,
        "display_name": display,
        "is_admin": bool(user.get("is_admin")),      # ASSUMED
        "is_owner": bool(user.get("is_owner")),       # ASSUMED
        "is_bot": bool(user.get("is_bot")),
        "deactivated": bool(user.get("deleted") or user.get("deactivated")),
        "team_id": user.get("team_id") or "",
    }


def build_grid_directory(client: SlackAdminClient) -> GridDirectory:
    """Assemble the deterministic user directory + roles + usergroup members."""
    directory = GridDirectory()

    for user in client.list_users():
        rec = _user_record(user)
        if rec["user_id"]:
            directory.principals.append(rec)

    # Roles: map each assignment's users to role ids. Field names defensive.
    for assignment in client.list_role_assignments():
        role_id = assignment.get("role_id") or assignment.get("role") or ""
        user_id = assignment.get("user_id") or assignment.get("entity_id") or ""
        if role_id and user_id:
            directory.roles_by_user.setdefault(user_id, []).append(role_id)

    # Usergroups → member user ids (for ACL principal expansion).
    for ug in client.list_usergroups(include_users=True):
        gid = ug.get("id") or ""
        users = ug.get("users") or []
        if gid:
            directory.usergroup_members[gid] = list(users)

    directory.principals.sort(key=lambda p: p["principal_id"])
    return directory


__all__ = [
    "SlackAdminClient",
    "SlackAdminError",
    "GridDirectory",
    "build_grid_directory",
    "SLACK_BASE_URL",
]
