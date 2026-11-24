"""
Unit tests for Slack citation deep-link construction (normalizer).

Regression guard for the malformed `https://slack.com.slack.com/...` host the
old fallback produced when no workspace domain was configured.
"""

import re

from infra.slack import normalizer as nz

# A correct host has exactly one workspace subdomain before slack.com.
GOOD_HOST_RE = re.compile(r"^https://[a-z0-9-]+\.slack\.com/archives/")


def test_bare_subdomain_builds_single_domain():
    url = nz._slack_deep_link("C123", "1780151456.181247", "loretestworkspace")
    assert url == "https://loretestworkspace.slack.com/archives/C123/p1780151456181247"
    assert GOOD_HOST_RE.match(url)


def test_full_host_is_not_double_appended():
    # _workspace_domain feeds normalized subdomains, but guard the builder too.
    sub = nz._normalize_subdomain("loretestworkspace.slack.com")
    assert sub == "loretestworkspace"


def test_url_form_is_normalized():
    assert nz._normalize_subdomain("https://acme.slack.com/") == "acme"


def test_bare_slack_com_is_rejected_no_double_domain():
    # The old bug: domain == "slack.com" → "slack.com.slack.com". Now rejected.
    assert nz._normalize_subdomain("slack.com") == ""
    url = nz._slack_deep_link("C123", "1780151456.181247", "")
    assert "slack.com.slack.com" not in url
    assert url == "https://app.slack.com/archives/C123/p1780151456181247"


def test_timestamp_packing_pads_microseconds():
    # 16-digit packed ts: 10-digit seconds + 6-digit microseconds.
    url = nz._slack_deep_link("C1", "1780151456.5", "acme")
    assert url.endswith("/p1780151456500000")
