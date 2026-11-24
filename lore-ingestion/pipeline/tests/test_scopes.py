"""Lock the ACL scope/principal ID formats — these strings are persisted in
Neo4j and *_scope_principals.json, so they must never drift. If a value here
changes, it is a breaking data-format change requiring a migration."""

from common import scopes


def test_scope_builders_exact_format():
    assert scopes.slack_channel("C123") == "slack:channel:C123"
    assert scopes.slack_workspace("T9") == "slack:workspace:T9"
    assert scopes.jira_project("ENG") == "jira:project:ENG"
    assert scopes.jira_seclevel("10001") == "jira:seclevel:10001"


def test_principal_builders_exact_format():
    assert scopes.slack_user("U42") == "slack:U42"
    assert scopes.jira_user("abc-123") == "jira:Uabc-123"   # no colon after U
    assert scopes.jira_group("g-uuid") == "jira:Gg-uuid"    # no colon after G
    assert scopes.gmail_user("alice@corp.com") == "gmail:user:alice@corp.com"
    assert scopes.person_email("alice@corp.com") == "person:alice@corp.com"
    assert scopes.person_email("BOB@Corp.COM") == "person:bob@corp.com"  # lowercased


def test_jira_group_is_idempotent():
    assert scopes.jira_group("jira:Galready") == "jira:Galready"
    assert scopes.jira_group("jira:Gx") == "jira:Gx"


def test_strip_and_detect_jira_user():
    assert scopes.is_jira_user("jira:Uabc") is True
    assert scopes.is_jira_user("slack:Uabc") is False
    assert scopes.strip_jira_user("jira:Uabc-123") == "abc-123"
    # Non-prefixed input is returned unchanged (matches str.removeprefix).
    assert scopes.strip_jira_user("abc-123") == "abc-123"


# ── Teams (legacy compound key — persisted, do not change) ────────────────────

def test_teams_channel_exact_format():
    """teams_channel() is the original LOR-64 scope format.
    It embeds both team_id and channel_id. Format is locked: any change
    breaks existing Neo4j ACL edges without a migration."""
    assert scopes.teams_channel("T-001", "C-001") == "teams:channel:T-001:C-001"
    assert scopes.teams_channel("team-guid", "chan-guid") == "teams:channel:team-guid:chan-guid"


def test_outlook_thread_exact_format():
    """outlook_thread() is the conversation-level scope for Outlook Mail.
    Format is locked: any change breaks existing Neo4j ACL edges."""
    assert scopes.outlook_thread("CONV-001") == "outlook:thread:CONV-001"
    assert scopes.outlook_thread("AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg") == \
        "outlook:thread:AAkALgAAAAAAHYQDEapmEc2byACqAC-EWg"


# ── MS365 channel scope (LOR-65 / LOR-66 — channel-only key) ─────────────────

def test_ms365_channel_scope_exact_format():
    """ms365_channel_scope() is the authoritative ACL scope format for Teams
    channels introduced in LOR-65 and used throughout the MS365 ACL chain:

        normalizer raw["scope_id"]  →  grouping thread dict  →  extraction
        discovery artifact (ms365_teams_scopes.json)  →  backend query gate

    Format is EXACTLY  ms365:channel:{channelId}  — channel ID only, no
    team_id prefix. This differs from the legacy teams_channel() format which
    embeds team_id. Do not change without a full data migration."""
    assert scopes.ms365_channel_scope("C123") == "ms365:channel:C123"
    assert scopes.ms365_channel_scope("19:abc123@thread.tacv2") == \
        "ms365:channel:19:abc123@thread.tacv2"


def test_ms365_channel_scope_not_teams_channel_format():
    """Confirm ms365_channel_scope does NOT produce the legacy teams:channel: prefix.
    A refactor that reverts to the old format would silently break ACL gating."""
    result = scopes.ms365_channel_scope("CHAN-001")
    assert not result.startswith("teams:channel:"), \
        "ms365_channel_scope must not use the legacy teams:channel: prefix"
    assert not result.startswith("teams:"), \
        "ms365_channel_scope must not use any teams: prefix"


def test_ms365_channel_scope_no_compound_key():
    """The format uses channel_id only — no team_id embedded.
    Providing just a channel_id must produce a two-segment key: ms365:channel:{id}."""
    result = scopes.ms365_channel_scope("CHAN-001")
    parts = result.split(":")
    # Must be exactly ["ms365", "channel", "CHAN-001"] — three segments
    assert len(parts) == 3, \
        f"Expected exactly 3 colon-separated segments, got {len(parts)}: {result!r}"
    assert parts[0] == "ms365"
    assert parts[1] == "channel"
    assert parts[2] == "CHAN-001"


def test_ms365_channel_scope_roundtrip():
    """ms365_channel_scope + is_ms365_channel form an inverse pair."""
    for channel_id in ("C123", "19:abc@thread.tacv2", "some-guid-here"):
        scope = scopes.ms365_channel_scope(channel_id)
        assert scopes.is_ms365_channel(scope), \
            f"is_ms365_channel should return True for {scope!r}"


# ── is_ms365_channel predicate ────────────────────────────────────────────────

def test_is_ms365_channel_true_for_valid_scope():
    assert scopes.is_ms365_channel("ms365:channel:C123") is True
    assert scopes.is_ms365_channel("ms365:channel:19:abc@thread.tacv2") is True


def test_is_ms365_channel_false_for_other_scopes():
    """Negative cases — every other scope format must return False."""
    assert scopes.is_ms365_channel("slack:channel:C123") is False
    assert scopes.is_ms365_channel("jira:project:ENG") is False
    assert scopes.is_ms365_channel("jira:seclevel:10001") is False
    assert scopes.is_ms365_channel("teams:channel:T-001:C-001") is False
    assert scopes.is_ms365_channel("outlook:thread:CONV-001") is False
    assert scopes.is_ms365_channel("ms365:user:someone") is False


def test_is_ms365_channel_false_for_empty_and_garbage():
    assert scopes.is_ms365_channel("") is False
    assert scopes.is_ms365_channel("ms365") is False
    assert scopes.is_ms365_channel("ms365:") is False
    assert scopes.is_ms365_channel("MS365:channel:C123") is False   # case-sensitive
