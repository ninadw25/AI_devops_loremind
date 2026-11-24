"""
infra/slack/capabilities.py
───────────────────────────
Slack connector capability detection — the single place that decides which
ingestion PATHWAY each feature takes, based on the token's granted scopes and
whether the workspace is on Enterprise Grid.

Why this exists
───────────────
~90% of Lore's clients run on Enterprise Grid and grant org-admin scopes
(`admin.conversations:read`, `admin.users:read`, `admin.roles:read`,
`auditlogs:read`, …). Those make identity, ACL/RBAC, and edit/delete detection
DETERMINISTIC. A standard (non-Grid) workspace only grants bot-level read scopes
(`channels:history`, `users:read.email`, …) and falls back to the heuristic /
snapshot-diff paths the pipeline uses today.

Rather than scatter `if "admin.conversations:read" in scopes` checks across the
fetcher, normalizer, and discovery, every pathway decision is centralised here.
Detection is automatic: read `X-OAuth-Scopes` from `auth.test` and the
`enterprise_id` field (present ⇒ Grid). A manual override is supported for the
rare case where auto-detection must be forced.

Nothing here performs IO — `detect()` takes the already-fetched `auth.test`
payload + scope header. This keeps it trivially unit-testable and keeps the
`infra.slack` layer one-way (no new external deps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


# ── Scope constants (grouped by the pathway they unlock) ─────────────

# Grid org-admin scopes — User token (xoxp) on an Enterprise Grid org.
ADMIN_CONVERSATIONS = "admin.conversations:read"
ADMIN_USERS = "admin.users:read"
ADMIN_ROLES = "admin.roles:read"
ADMIN_USERGROUPS = "admin.usergroups:read"
ADMIN_TEAMS = "admin.teams:read"
AUDITLOGS = "auditlogs:read"
# eDiscovery app scope (separate Org-approved app type). Not in the standard
# scope picker; captured if the token happens to carry it.
DISCOVERY = "discovery:read"

# Bot/standard read scopes — Bot token (xoxb) on any workspace.
CHANNELS_HISTORY = "channels:history"
GROUPS_HISTORY = "groups:history"
IM_HISTORY = "im:history"
MPIM_HISTORY = "mpim:history"
USERS_READ = "users:read"
USERS_EMAIL = "users:read.email"
USERS_PROFILE = "users.profile:read"
USERGROUPS_READ = "usergroups:read"
REACTIONS_READ = "reactions:read"
FILES_READ = "files:read"
PINS_READ = "pins:read"
BOOKMARKS_READ = "bookmarks:read"
TEAM_READ = "team:read"
SEARCH_PUBLIC = "search:read.public"


class Tier(str, Enum):
    """Coarse capability tier — the headline pathway selector."""
    GRID_ADMIN = "grid_admin"      # Grid + admin scopes → deterministic everywhere
    BOT_STANDARD = "bot_standard"  # bot read scopes only → heuristic / snapshot-diff


class AclPathway(str, Enum):
    ADMIN_ENUM = "admin_enum"          # admin.conversations.* — org-wide, no join needed
    BOT_MEMBERSHIP = "bot_membership"  # conversations.members — bot must be a member


class IdentityPathway(str, Enum):
    ADMIN_DIRECTORY = "admin_directory"  # admin.users.list + admin.roles → full directory + roles
    BOT_PROFILE = "bot_profile"          # users.list + users.profile (email/title if granted)
    HEURISTIC = "heuristic"              # name/local-part inference only


class EditDeletePathway(str, Enum):
    # Confirmed against Slack docs (2025-2026):
    #  • Discovery API is the ONLY deterministic, org-wide, RETROACTIVE source
    #    of message edits/deletes/tombstones on Grid (needs an Org-Owner user
    #    token + a Slack-APPROVED Discovery app — a heavy enablement gate).
    #  • Events API message_changed/message_deleted are real-time but
    #    FORWARD-ONLY and only for channels the app is a member of.
    #  • Audit Logs API does NOT carry message content edits/deletes (it is
    #    admin/security/config events only) — deliberately NOT a pathway here.
    #  • Otherwise: diff this fetch against the prior snapshot.
    DISCOVERY_API = "discovery_api"  # Grid eDiscovery — historical + tombstones
    EVENTS = "events"                # real-time, forward-only, in-channel only
    SNAPSHOT_DIFF = "snapshot_diff"  # diff this fetch vs prior snapshot (fallback)


@dataclass(frozen=True)
class SlackCapabilities:
    """Resolved per-feature pathways for one Slack token.

    Build via `detect()`. Treat as immutable; pass it down to the fetcher,
    discovery, and normalizer so every stage agrees on the chosen pathway.
    """

    granted_scopes: frozenset[str]
    is_grid: bool
    is_enterprise_install: bool
    token_kind: str            # "bot" | "user" | "unknown"
    enterprise_id: str = ""
    team_id: str = ""
    # When True, edit/delete real-time events are assumed wired (socket/events
    # subscription running). Detection can't see this from a token, so it is an
    # explicit operator signal, off by default.
    events_subscribed: bool = False

    # ── raw scope predicates ─────────────────────────────────────────
    def has(self, scope: str) -> bool:
        return scope in self.granted_scopes

    def has_any(self, scopes: Iterable[str]) -> bool:
        return any(s in self.granted_scopes for s in scopes)

    # ── admin-path predicates (also require Grid) ────────────────────
    @property
    def can_admin_conversations(self) -> bool:
        return self.is_grid and self.has(ADMIN_CONVERSATIONS)

    @property
    def can_admin_users(self) -> bool:
        return self.is_grid and self.has(ADMIN_USERS)

    @property
    def can_admin_roles(self) -> bool:
        return self.is_grid and self.has(ADMIN_ROLES)

    @property
    def can_admin_usergroups(self) -> bool:
        return self.is_grid and self.has(ADMIN_USERGROUPS)

    @property
    def can_auditlogs(self) -> bool:
        return self.is_grid and self.has(AUDITLOGS)

    @property
    def can_discovery(self) -> bool:
        return self.is_grid and self.has(DISCOVERY)

    # ── bot-path predicates (any workspace) ──────────────────────────
    @property
    def can_read_history(self) -> bool:
        return self.has_any((CHANNELS_HISTORY, GROUPS_HISTORY, IM_HISTORY, MPIM_HISTORY))

    @property
    def can_read_email(self) -> bool:
        return self.has(USERS_EMAIL)

    @property
    def can_read_profile(self) -> bool:
        return self.has(USERS_PROFILE)

    @property
    def can_usergroups(self) -> bool:
        return self.has(USERGROUPS_READ) or self.can_admin_usergroups

    @property
    def can_reactions(self) -> bool:
        return self.has(REACTIONS_READ)

    @property
    def can_files(self) -> bool:
        return self.has(FILES_READ)

    @property
    def can_salience(self) -> bool:
        """Pins/bookmarks signal — curated = high-importance content."""
        return self.has_any((PINS_READ, BOOKMARKS_READ))

    # ── resolved pathways (the decisions stages consume) ─────────────
    @property
    def tier(self) -> Tier:
        return Tier.GRID_ADMIN if self.can_admin_conversations else Tier.BOT_STANDARD

    @property
    def acl_pathway(self) -> AclPathway:
        if self.can_admin_conversations:
            return AclPathway.ADMIN_ENUM
        return AclPathway.BOT_MEMBERSHIP

    @property
    def identity_pathway(self) -> IdentityPathway:
        if self.can_admin_users:
            return IdentityPathway.ADMIN_DIRECTORY
        if self.has(USERS_READ):
            return IdentityPathway.BOT_PROFILE
        return IdentityPathway.HEURISTIC

    @property
    def editdelete_pathway(self) -> EditDeletePathway:
        # Best determinism first. Discovery API is the only retroactive,
        # org-wide source (Grid + approved app). Events is real-time but
        # forward-only. Audit Logs is intentionally absent — it does not
        # carry message content. Snapshot-diff is the universal fallback.
        if self.can_discovery:
            return EditDeletePathway.DISCOVERY_API
        if self.events_subscribed:
            return EditDeletePathway.EVENTS
        return EditDeletePathway.SNAPSHOT_DIFF

    # ── reporting ────────────────────────────────────────────────────
    def summary(self) -> dict:
        """A flat, greppable dict for STAGE_SUMMARY logging / debugging."""
        return {
            "tier": self.tier.value,
            "is_grid": self.is_grid,
            "token_kind": self.token_kind,
            "enterprise_id": self.enterprise_id or None,
            "acl_pathway": self.acl_pathway.value,
            "identity_pathway": self.identity_pathway.value,
            "editdelete_pathway": self.editdelete_pathway.value,
            "can_read_email": self.can_read_email,
            "can_read_profile": self.can_read_profile,
            "can_usergroups": self.can_usergroups,
            "can_reactions": self.can_reactions,
            "can_files": self.can_files,
            "can_salience": self.can_salience,
            "scope_count": len(self.granted_scopes),
        }


def parse_scopes(scopes_header: Optional[str]) -> frozenset[str]:
    """Parse a comma-separated `X-OAuth-Scopes` header into a scope set."""
    if not scopes_header:
        return frozenset()
    return frozenset(s.strip() for s in scopes_header.split(",") if s.strip())


def _infer_token_kind(auth_test: dict, token: Optional[str]) -> str:
    # Bot tokens start xoxb-, user tokens xoxp-. auth.test also carries a
    # `bot_id` for bot tokens. Prefer the prefix (unambiguous), fall back to
    # the payload shape.
    if token:
        if token.startswith("xoxb-"):
            return "bot"
        if token.startswith("xoxp-"):
            return "user"
    if auth_test.get("bot_id"):
        return "bot"
    if auth_test.get("user_id") and not auth_test.get("bot_id"):
        return "user"
    return "unknown"


def detect(
    auth_test: dict,
    scopes_header: Optional[str],
    token: Optional[str] = None,
    *,
    force_grid: Optional[bool] = None,
    events_subscribed: bool = False,
) -> SlackCapabilities:
    """Resolve capabilities from an `auth.test` payload + scope header.

    Args:
        auth_test: the JSON dict returned by `auth.test`.
        scopes_header: the `X-OAuth-Scopes` response header value.
        token: the raw token (used only to infer bot vs user kind).
        force_grid: manual override for Grid detection (None = auto-detect).
        events_subscribed: operator signal that a real-time message-events
            listener is running (enables the EVENTS edit/delete pathway).

    Grid is auto-detected from `enterprise_id` (present ⇒ the workspace belongs
    to an Enterprise Grid org). `is_enterprise_install` indicates an org-wide
    install. Either can be overridden via `force_grid`.
    """
    enterprise_id = str(auth_test.get("enterprise_id") or "")
    is_enterprise_install = bool(auth_test.get("is_enterprise_install"))
    auto_grid = bool(enterprise_id) or is_enterprise_install
    is_grid = auto_grid if force_grid is None else bool(force_grid)

    return SlackCapabilities(
        granted_scopes=parse_scopes(scopes_header),
        is_grid=is_grid,
        is_enterprise_install=is_enterprise_install,
        token_kind=_infer_token_kind(auth_test, token),
        enterprise_id=enterprise_id,
        team_id=str(auth_test.get("team_id") or ""),
        events_subscribed=events_subscribed,
    )


__all__ = [
    "Tier",
    "AclPathway",
    "IdentityPathway",
    "EditDeletePathway",
    "SlackCapabilities",
    "detect",
    "parse_scopes",
]
