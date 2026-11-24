"""
Tests for infra/slack/capabilities.py — capability detection + pathway choice.

Locks the three workspace shapes we ingest:
  • Grid-admin (Enterprise Grid + admin scopes) → deterministic everywhere
  • Bot-standard (bot read scopes only)         → heuristic / snapshot-diff
  • Minimal (channels:history only)             → barely-readable fallback
"""

from infra.slack import capabilities as cap
from infra.slack.capabilities import (
    AclPathway,
    EditDeletePathway,
    IdentityPathway,
    Tier,
    detect,
    parse_scopes,
)

# ── representative auth.test payloads ────────────────────────────────

GRID_AUTH = {
    "ok": True,
    "team_id": "T123",
    "user_id": "U999",
    "enterprise_id": "E0ABC",          # ← present ⇒ Grid
    "is_enterprise_install": True,
}

STANDARD_AUTH = {
    "ok": True,
    "team_id": "T123",
    "user_id": "U999",
    "bot_id": "B123",                  # bot token
}

GRID_ADMIN_SCOPES = (
    "admin.conversations:read,admin.users:read,admin.roles:read,"
    "admin.usergroups:read,admin.teams:read,auditlogs:read,discovery:read,"
    "channels:read,users:read,users:read.email"
)

BOT_RICH_SCOPES = (
    "channels:history,groups:history,users:read,users:read.email,"
    "users.profile:read,usergroups:read,reactions:read,files:read,"
    "pins:read,bookmarks:read,team:read"
)


def test_parse_scopes_handles_blank_and_whitespace():
    assert parse_scopes(None) == frozenset()
    assert parse_scopes("") == frozenset()
    assert parse_scopes(" a , b ,, c ") == frozenset({"a", "b", "c"})


def test_grid_admin_token_picks_deterministic_pathways():
    c = detect(GRID_AUTH, GRID_ADMIN_SCOPES, token="xoxp-abc")
    assert c.is_grid is True
    assert c.token_kind == "user"
    assert c.tier is Tier.GRID_ADMIN
    assert c.acl_pathway is AclPathway.ADMIN_ENUM
    assert c.identity_pathway is IdentityPathway.ADMIN_DIRECTORY
    # discovery:read present ⇒ retroactive edit/delete source
    assert c.editdelete_pathway is EditDeletePathway.DISCOVERY_API
    assert c.can_admin_conversations and c.can_admin_users and c.can_admin_roles


def test_admin_scopes_without_grid_do_not_unlock_admin_path():
    # Same admin scopes but a non-Grid workspace (no enterprise_id) → the
    # admin methods aren't usable, so we must fall back.
    c = detect(STANDARD_AUTH, GRID_ADMIN_SCOPES, token="xoxp-abc")
    assert c.is_grid is False
    assert c.can_admin_conversations is False
    assert c.tier is Tier.BOT_STANDARD
    assert c.acl_pathway is AclPathway.BOT_MEMBERSHIP


def test_bot_standard_token_uses_heuristic_fallbacks():
    c = detect(STANDARD_AUTH, BOT_RICH_SCOPES, token="xoxb-abc")
    assert c.is_grid is False
    assert c.token_kind == "bot"
    assert c.tier is Tier.BOT_STANDARD
    assert c.acl_pathway is AclPathway.BOT_MEMBERSHIP
    assert c.identity_pathway is IdentityPathway.BOT_PROFILE
    assert c.editdelete_pathway is EditDeletePathway.SNAPSHOT_DIFF
    assert c.can_read_email and c.can_read_profile
    assert c.can_usergroups and c.can_salience and c.can_files


def test_events_subscription_enables_events_pathway():
    c = detect(STANDARD_AUTH, BOT_RICH_SCOPES, token="xoxb-abc", events_subscribed=True)
    assert c.editdelete_pathway is EditDeletePathway.EVENTS


def test_minimal_token_degrades_to_heuristic_identity():
    c = detect(STANDARD_AUTH, "channels:history", token="xoxb-abc")
    assert c.can_read_history is True
    assert c.identity_pathway is IdentityPathway.HEURISTIC
    assert c.can_read_email is False
    assert c.can_salience is False


def test_force_grid_override():
    # Operator can force the Grid path even if auto-detect says otherwise.
    c = detect(STANDARD_AUTH, GRID_ADMIN_SCOPES, token="xoxp-abc", force_grid=True)
    assert c.is_grid is True
    assert c.can_admin_conversations is True


def test_summary_is_flat_and_greppable():
    c = detect(GRID_AUTH, GRID_ADMIN_SCOPES, token="xoxp-abc")
    s = c.summary()
    assert s["tier"] == "grid_admin"
    assert s["editdelete_pathway"] == "discovery_api"
    assert s["acl_pathway"] == "admin_enum"
    assert isinstance(s["scope_count"], int) and s["scope_count"] > 0
