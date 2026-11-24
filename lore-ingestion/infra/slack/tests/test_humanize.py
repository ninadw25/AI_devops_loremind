"""
W2: tests for humanize_slack_text — Slack mrkdwn → human-readable text so the
extractor LLM reads names, not opaque ids.
"""

from infra.slack.normalizer import humanize_slack_text

USER_CACHE = {
    "U0B8KEP3SKS": {"id": "U0B8KEP3SKS",
                    "profile": {"display_name": "ravi", "real_name": "Ravi Kumar"}},
    "U0B7MFN1H9B": {"id": "U0B7MFN1H9B",
                    "profile": {"real_name": "Tanvi Chaughule"}},  # no display_name
    "UNAME": {"id": "UNAME", "name": "legacy_name"},               # only top-level name
}


def test_user_mention_resolves_to_display_name():
    assert humanize_slack_text("hey <@U0B8KEP3SKS> ping", USER_CACHE) == "hey @ravi ping"


def test_user_mention_falls_back_to_real_name_then_name():
    assert humanize_slack_text("<@U0B7MFN1H9B>", USER_CACHE) == "@Tanvi Chaughule"
    assert humanize_slack_text("<@UNAME>", USER_CACHE) == "@legacy_name"


def test_unknown_user_id_keeps_readable_fallback():
    # No cache hit, no label → keep an @id rather than an opaque <@…>.
    assert humanize_slack_text("<@UZZZZZZ>", USER_CACHE) == "@UZZZZZZ"
    # With a piped label, prefer the label.
    assert humanize_slack_text("<@UZZZZZZ|fallback>", USER_CACHE) == "@fallback"


def test_channel_ref():
    assert humanize_slack_text("see <#C123ABC|general>") == "see #general"
    assert humanize_slack_text("see <#C123ABC>") == "see #C123ABC"


def test_special_mentions():
    assert humanize_slack_text("<!here> look") == "@here look"
    assert humanize_slack_text("<!channel>") == "@channel"
    assert humanize_slack_text("<!subteam^S999|@frontend> ship it") == "@frontend ship it"


def test_links():
    assert humanize_slack_text("docs <https://x.com/a|the spec> here") == "docs the spec here"
    assert humanize_slack_text("<https://x.com/a>") == "https://x.com/a"
    assert humanize_slack_text("<mailto:a@b.com|a@b.com>") == "a@b.com"


def test_entity_decode_and_empty():
    assert humanize_slack_text("a &amp; b &lt;tag&gt;") == "a & b <tag>"
    assert humanize_slack_text("") == ""
    assert humanize_slack_text("plain text, nothing to do") == "plain text, nothing to do"


def test_no_user_cache_does_not_crash():
    # Defensive: missing cache → @id fallback, never raises.
    assert humanize_slack_text("<@U0B8KEP3SKS> hi", None) == "@U0B8KEP3SKS hi"


def test_combined_realistic_message():
    msg = "<@U0B8KEP3SKS> can you review <#C1|eng> spec at <https://x.com|here>? cc <!here>"
    out = humanize_slack_text(msg, USER_CACHE)
    assert out == "@ravi can you review #eng spec at here? cc @here"
