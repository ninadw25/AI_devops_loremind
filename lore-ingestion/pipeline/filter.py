"""
pipeline/filter.py
──────────────────
Post-processing pipeline for raw KnowledgeEvents (Gmail and Jira).

Designed to work for ANY organization without hardcoded rules.

Pipeline stages:
  1. FILTER — signal-based noise detection (universal + org-config)
  2. CLEAN  — fix body text (HTML entities, literal \\n, strip URLs/tags)
  3. CLASSIFY — LLM-powered semantic classification (via Claude)
  4. OUTPUT — write clean JSONL with updated fields + regenerated embedding_text

Noise detection strategy (generalized):
  - Universal noise domains: a small curated set that are NEVER org-relevant
    (google.com, social media, email infrastructure, etc.)
  - Org-specific: learned via auto-discovery (high-volume non-org senders
    with no replies from org employees) and stored in org config
  - Zero-body external: external emails with no body content
  - Allowlist: explicitly allowed domains bypass all filters

Usage:
  python -m gmail.filter --input output/org_gmail_2026-03-01.jsonl --org myorg
  python -m gmail.filter ... --discover-noise    # run auto-discovery first
  python -m gmail.filter ... --skip-llm          # skip LLM classification
"""

import json
import re
import time
from html import unescape as html_unescape
from pathlib import Path
from collections import Counter
from typing import Optional

from common import scopes
from common.config import get_settings
from common.logger import get_logger
from common.org_config import load_org_config, save_org_config
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from pipeline.input_selection import latest_nonempty_path

log = get_logger("pipeline.filter")


# ══════════════════════════════════════════════════════════════════════
# UNIVERSAL NOISE DEFINITIONS
# These apply to ALL organizations — truly automated/mass senders
# that never contain org knowledge regardless of company.
# ══════════════════════════════════════════════════════════════════════

# Universal noise domains — platforms that ONLY send automated mail.
# This is intentionally small. Org-specific domains go in org config.
UNIVERSAL_NOISE_DOMAINS = {
    # Google system
    "google.com",
    # Social media notifications (never contain org knowledge)
    "facebookmail.com", "business-updates.facebook.com",
    "linkedin.com", "instagram.com", "twitter.com", "twittermail.com",
    # Email infrastructure (the sender is a relay, not a person)
    "sendgrid.net", "mailgun.org", "mailchimp.com", "sendinblue.com",
    "constantcontact.com",
    # Newsletter platforms
    "substack.com", "medium.com", "beehiiv.com",
    # Scheduling platforms
    "calendly.com", "tidycal.com",
}


# ══════════════════════════════════════════════════════════════════════
# AUTO-DISCOVERY: Learn noise domains for a specific org
# ══════════════════════════════════════════════════════════════════════

def discover_noise_domains(
    input_path: Path,
    org_domains: list[str],
    min_volume: int = 50,
    max_reply_ratio: float = 0.02,
) -> list[dict]:
    """
    Analyze a raw JSONL file and discover external domains that are
    likely noise for this org.

    Heuristic: a domain is noise if:
      - It sends >= min_volume emails to the org
      - Org employees almost never reply to it (reply ratio < max_reply_ratio)
      - It's not in the org's own domains

    Returns list of dicts: [{"domain": ..., "volume": ..., "reply_ratio": ...}]
    """
    log.info("Auto-discovering noise domains (min_volume=%d, max_reply_ratio=%.2f)",
             min_volume, max_reply_ratio)

    org_domain_set = set(d.lower() for d in org_domains)

    # Count: how many emails each external domain sends TO the org
    inbound_count: Counter = Counter()
    # Count: how many emails org employees send TO each external domain
    outbound_count: Counter = Counter()

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            sender = event.get("primary_actor", "").replace("email:", "").lower()
            sender_domain = sender.split("@")[-1] if "@" in sender else ""

            if not sender_domain:
                continue

            actors = event.get("actors", [])
            recipient_domains = set()
            for a in actors:
                email = (a.get("email") or a.get("id", "").replace("email:", "")).lower()
                if "@" in email:
                    recipient_domains.add(email.split("@")[-1])

            sender_is_org = sender_domain in org_domain_set

            if sender_is_org:
                # Org employee sending outbound — track which external domains they write to
                for rd in recipient_domains:
                    if rd not in org_domain_set:
                        outbound_count[rd] += 1
            else:
                # External sender — track inbound volume
                inbound_count[sender_domain] += 1

    # Find domains with high inbound volume but near-zero outbound (replies)
    discovered = []
    for domain, volume in inbound_count.most_common():
        if domain in org_domain_set:
            continue
        if volume < min_volume:
            break  # sorted by count, so all subsequent are lower

        replies = outbound_count.get(domain, 0)
        ratio = replies / volume if volume > 0 else 0

        if ratio <= max_reply_ratio:
            discovered.append({
                "domain": domain,
                "inbound_volume": volume,
                "outbound_replies": replies,
                "reply_ratio": round(ratio, 4),
            })

    log.info("Discovered %d noise domains (high inbound, near-zero replies)", len(discovered))
    for d in discovered[:20]:
        log.info("  %-40s vol=%d replies=%d ratio=%.3f",
                 d["domain"], d["inbound_volume"], d["outbound_replies"], d["reply_ratio"])
    if len(discovered) > 20:
        log.info("  ... and %d more", len(discovered) - 20)

    return discovered


# ══════════════════════════════════════════════════════════════════════
# STAGE 1: FILTER (generalized, signal-based)
# ══════════════════════════════════════════════════════════════════════

# Bump when is_noise's signal heuristics change — the analogue of
# normalizer_version. A change re-classifies the events table as an UPDATE
# (fills noise_class), never a re-fetch or a file-to-file re-run
# (05_SYNC_AND_LEDGER §3.2).
FILTER_VERSION = "gmail-filter-1"


def is_noise(
    event: dict,
    org_noise_domains: Optional[set[str]] = None,
    org_allowlisted: Optional[set[str]] = None,
) -> tuple[bool, str]:
    """
    Returns (True, reason) if this event should be filtered out.
    Returns (False, "") if it should be kept.

    Uses universal signals + org-specific learned noise domains.
    """
    tags = event.get("tags", [])
    primary = event.get("primary_actor", "").replace("email:", "")
    sender_domain = primary.split("@")[-1].lower() if "@" in primary else ""
    body = event.get("body", "")

    # 0. Allowlist check — skip all filters for explicitly allowed domains
    if org_allowlisted and sender_domain in org_allowlisted:
        return False, ""

    # 1. Universal noise domains
    if sender_domain in UNIVERSAL_NOISE_DOMAINS:
        return True, "noise_domain_universal"
    # Check parent domain for subdomains: "mail.beehiiv.com" → "beehiiv.com"
    domain_parts = sender_domain.split(".")
    if len(domain_parts) > 2:
        parent = ".".join(domain_parts[-2:])
        if parent in UNIVERSAL_NOISE_DOMAINS:
            return True, "noise_domain_universal"

    # 2. Org-specific learned noise domains
    if org_noise_domains:
        if sender_domain in org_noise_domains:
            return True, "noise_domain_discovered"
        if len(domain_parts) > 2:
            parent = ".".join(domain_parts[-2:])
            if parent in org_noise_domains:
                return True, "noise_domain_discovered"

    # 3. Zero-body messages — no content to extract knowledge from.
    #    Keep internal zero-body (quick replies like "ok", "done").
    if (not body or len(body.strip()) == 0) and "internal" not in tags:
        return True, "empty_body_external"

    return False, ""


# ══════════════════════════════════════════════════════════════════════
# JIRA NOISE FILTER
# ══════════════════════════════════════════════════════════════════════

_JIRA_AUTO_COMMENT_RE = re.compile(
    r"^(this (issue|ticket|item) (has been|was) automatically"
    r"|automation rule"
    r"|moved to .+ (by|via) automation"
    r"|\[automated\]"
    r"|jira (service management|automation))",
    re.IGNORECASE,
)

_JIRA_CHANGELOG_TYPES = {"jira.issue.transitioned", "jira.issue.reassigned", "jira.issue.resolved"}


def load_jira_bot_ids(artifact_path: Path) -> frozenset[str]:
    """Read the discovery artifact and return accountIds of app-type principals.

    Used to catch bot-created issues where actors[] is empty and only
    primary_actor (a raw accountId string) is available.
    Returns an empty frozenset when the artifact is absent.
    """
    if not artifact_path.exists():
        return frozenset()
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    bot_ids: set[str] = set()
    for p in artifact.get("principals", []):
        if p.get("account_type") == "app":
            # principal_id format: "jira:U{accountId}"
            pid = p.get("principal_id", "")
            raw_id = scopes.strip_jira_user(pid)
            if raw_id:
                bot_ids.add(raw_id)
    return frozenset(bot_ids)


def is_jira_noise(
    event: dict,
    bot_account_ids: frozenset[str] | None = None,
    include_archived: bool = False,
) -> tuple[bool, str]:
    """Return (True, reason) if this Jira event should be dropped.

    Signals (checked in priority order):
    - jira_bot_actor          actors[0].accountType == "app"  (comments)
                              OR primary_actor in bot_account_ids  (issues)
    - jira_archived           issue is archived and include_archived=False
    - jira_changelog          status/assignee/resolved transitions
    - jira_empty_body         no text content after strip
    - jira_automated_comment  body matches known automation phrases
    """
    event_type = event.get("event_type", "")
    body = (event.get("body", "") or "").strip()
    actors = event.get("actors", [])

    # Bot actor via actors dict (comments carry full author object with accountType)
    if actors and actors[0].get("accountType") == "app":
        return True, "jira_bot_actor"

    # Bot actor via primary_actor cross-reference (issues have actors=[])
    if bot_account_ids:
        primary = event.get("primary_actor", "")
        if primary in bot_account_ids:
            return True, "jira_bot_actor"

    # Archived issues carry no new signal and clutter the graph
    if not include_archived:
        raw = event.get("raw") or {}
        fields = raw.get("fields") or {}
        if fields.get("archived") is True or "archived" in event.get("tags", []):
            return True, "jira_archived"

    # Changelog events are machine-generated structural deltas; the issue event
    # already captures the current state, so these add no extraction value.
    if event_type in _JIRA_CHANGELOG_TYPES:
        return True, "jira_changelog"

    # No body — nothing to extract
    if not body:
        return True, "jira_empty_body"

    # Automated comment phrases (e.g. Jira Automation rule triggers)
    if _JIRA_AUTO_COMMENT_RE.match(body):
        return True, "jira_automated_comment"

    return False, ""


# ══════════════════════════════════════════════════════════════════════
# OUTLOOK MAIL NOISE FILTER
# ══════════════════════════════════════════════════════════════════════

# Sender-domain prefixes that are never human senders.
# Matched against the full subdomain chain so "bounces.noreply.example.com"
# is caught by "noreply" and "no-reply.example.com" is caught by "no-reply".
_OUTLOOK_NOREPLY_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications", "notification",
    "mailer-daemon", "mailerdaemon",
    "bounce", "bounces",
    "auto-reply", "autoreply",
)

# Subject-line keywords that identify automated / out-of-office responses.
# All matched case-insensitively against the full subject string.
_OUTLOOK_AUTO_SUBJECTS = (
    "out of office",
    "autoreply",
    "auto reply",
    "automatic reply",
    "delivery report",
    "undeliverable",
    "delivery failure",
    "mail delivery failed",
    "returned mail",
    "read receipt",
)


def is_outlook_noise(event: dict) -> tuple[bool, str]:
    """Return (True, reason) if this Outlook Mail event should be dropped.

    Signals (checked in priority order):

    1. outlook_draft            raw["isDraft"] is True
    2. outlook_noreply_domain   sender domain / any subdomain starts with a
                                known noreply prefix
    3. outlook_auto_subject     subject contains an automated-response keyword
    4. outlook_bulk_mail        raw["internetMessageHeaders"] contains
                                List-Unsubscribe (RFC 2369 bulk-mail marker)
    5. outlook_empty_body       effective body < 5 chars after HTML strip

    Returns (False, "") when none apply — event is kept.
    """
    raw = event.get("raw") or {}
    title = (event.get("title") or "").lower()
    body = (event.get("body") or "")

    # 1. Draft messages — never user-sent, never relevant
    if raw.get("isDraft") is True:
        return True, "outlook_draft"

    # 2. Noreply / automated sender domain — check both the local part and
    #    every non-TLD label in the domain so all of these match:
    #      noreply@corp.com              (local part exact)
    #      no-reply@services.example.com (local part normalised)
    #      notifications@github.com      (local part exact)
    #      mailer-daemon@smtp.corp.com   (local part normalised)
    #      bounces.12345@em.sendgrid.net (local part starts-with prefix)
    primary = (event.get("primary_actor") or "").replace("email:", "").lower()
    if "@" in primary:
        local_part, sender_domain = primary.split("@", 1)
        domain_labels = sender_domain.split(".")[:-1]   # exclude TLD

        def _matches_noreply(s: str) -> bool:
            """True if s equals or starts with any noreply prefix after hyphen-norm."""
            s_norm = s.replace("-", "")
            for prefix in _OUTLOOK_NOREPLY_PREFIXES:
                p_norm = prefix.replace("-", "")
                if s_norm == p_norm or s_norm.startswith(p_norm):
                    return True
            return False

        # Check local part first (most common pattern), then domain labels
        if _matches_noreply(local_part) or any(_matches_noreply(lbl) for lbl in domain_labels):
            return True, "outlook_noreply_domain"

    # 3. Automated subject keywords
    for kw in _OUTLOOK_AUTO_SUBJECTS:
        if kw in title:
            return True, "outlook_auto_subject"

    # 4. Bulk-mail marker (List-Unsubscribe header — RFC 2369)
    headers = raw.get("internetMessageHeaders") or []
    if isinstance(headers, list):
        for h in headers:
            if isinstance(h, dict) and (h.get("name") or "").lower() == "list-unsubscribe":
                return True, "outlook_bulk_mail"
    elif isinstance(headers, str) and "list-unsubscribe" in headers.lower():
        return True, "outlook_bulk_mail"

    # 5. Empty body after HTML strip (< 5 effective chars)
    effective = _HTML_TAG_RE.sub("", body).strip()
    if len(effective) < 5:
        return True, "outlook_empty_body"

    return False, ""


def run_outlook_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    allow_empty: bool = False,
) -> dict:
    """Filter Outlook Mail JSONL: drop drafts, noreply senders, auto-replies,
    bulk mail, and empty messages.

    Reads  output/raw/{org}_outlook_mail*.jsonl
    Writes output/filtered/{org}_outlook_mail_filtered.jsonl  (kept)
           output/raw/{org}_outlook_mail_noise.jsonl           (dropped, for audit)
    """
    from common.output_paths import OutlookPaths
    start = time.time()

    paths = OutlookPaths(org, output_dir)
    paths.makedirs()

    output_path = paths.filtered_mail_jsonl
    noise_path  = output_dir / "raw" / f"{org}_outlook_mail_noise.jsonl"

    total = kept = noise_count = 0
    noise_reasons: Counter = Counter()

    log.info("=" * 55)
    log.info("Outlook Mail Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("=" * 55)

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path,  "w", encoding="utf-8") as fnoise,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            event = json.loads(line)

            is_junk, reason = is_outlook_noise(event)
            if is_junk:
                noise_count += 1
                noise_reasons[reason] += 1
                event["_filter_reason"] = reason
                fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

    elapsed = time.time() - start
    drop_pct = round(noise_count * 100 / total, 1) if total else 0
    stats = {
        "org": org,
        "source": "outlook_mail",
        "input_file":  str(input_path),
        "output_file": str(output_path),
        "noise_file":  str(noise_path),
        "total_input": total,
        "kept":        kept,
        "dropped":     noise_count,
        "drop_rate_pct": drop_pct,
        "noise_reasons": dict(noise_reasons.most_common()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("FILTER COMPLETE — %s (outlook_mail)", org)
    log.info("  Kept: %d / %d (%.1f%% dropped)", kept, total, drop_pct)
    for reason, count in noise_reasons.most_common():
        log.info("    %-30s %6d", reason, count)

    check_nonempty("filter", count=kept, what="Outlook Mail events kept",
                   allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# MICROSOFT TEAMS NOISE FILTER
# ══════════════════════════════════════════════════════════════════════

# Regex that matches a string composed entirely of Unicode emoji + whitespace.
# The \U0001F000-\U0001FFFF range covers most emoji blocks;
# \u2000-\u27BF covers misc symbols and dingbats;
# \uFE00-\uFE0F covers variation selectors.
_TEAMS_EMOJI_ONLY_RE = re.compile(
    r"^[\s"
    r"\U0001F000-\U0001FFFF"   # emoticons, symbols, etc.
    r"\u2000-\u27BF"            # misc symbols, dingbats
    r"\uFE00-\uFE0F"            # variation selectors
    r"\u200D"                   # zero-width joiner (compound emoji)
    r"\uFE0F"                   # variation selector-16
    r"]*$",
    re.UNICODE,
)


def is_ms_teams_noise(event: dict) -> tuple[bool, str]:
    """Return (True, reason) if this Teams message should be dropped.

    Signals (checked in priority order):

    1. teams_system_event   raw["message_type"] == "systemEventMessage"
                            (user joined/left channel, channel renamed, etc.)
    2. teams_bot_sender     raw["from"]["application"] is not None — the
                            normalizer sets primary_actor = "teams:{id}" for
                            application senders; detected here via the
                            actor's is_bot flag or the sender id prefix.
                            In the normalized event, bot messages carry
                            is_bot=True on the first actor.
    3. teams_empty_body     effective body (after HTML strip) < 5 chars
    4. teams_emoji_only     body is entirely emoji / whitespace — no text
                            content to extract knowledge from

    Returns (False, "") when none apply — event is kept.
    """
    raw = event.get("raw") or {}
    body = (event.get("body") or "")
    actors = event.get("actors") or []

    # 1. System events — machine-generated structural notifications
    if raw.get("message_type") == "systemEventMessage":
        return True, "teams_system_event"

    # 2. Bot sender — normalizer marks is_bot=True on the first actor for
    #    application-type senders (from.application is not None in Graph API).
    if actors and actors[0].get("is_bot"):
        return True, "teams_bot_sender"

    # 3. Effective body — strip HTML once, used by both checks below
    effective = _HTML_TAG_RE.sub("", body).strip()

    # 4. Emoji-only body — reactions / single-emoji acknowledgements.
    #    Checked before the length gate so a single 👍 gets reason
    #    "teams_emoji_only" rather than "teams_empty_body".
    if effective and _TEAMS_EMOJI_ONLY_RE.match(effective):
        return True, "teams_emoji_only"

    # 5. Empty body after HTML strip (< 5 effective chars)
    if len(effective) < 5:
        return True, "teams_empty_body"

    return False, ""


def run_ms_teams_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    allow_empty: bool = False,
) -> dict:
    """Filter Teams JSONL: drop system events, bot messages, emoji-only and
    empty messages.

    Reads  output/raw/{org}_ms_teams*.jsonl
    Writes output/filtered/{org}_ms_teams_filtered.jsonl  (kept)
           output/raw/{org}_ms_teams_noise.jsonl           (dropped, for audit)
    """
    from common.output_paths import OutlookPaths
    start = time.time()

    paths = OutlookPaths(org, output_dir)
    paths.makedirs()

    output_path = paths.filtered_teams_jsonl
    noise_path  = output_dir / "raw" / f"{org}_ms_teams_noise.jsonl"

    total = kept = noise_count = 0
    noise_reasons: Counter = Counter()

    log.info("=" * 55)
    log.info("Microsoft Teams Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("=" * 55)

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path,  "w", encoding="utf-8") as fnoise,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            event = json.loads(line)

            is_junk, reason = is_ms_teams_noise(event)
            if is_junk:
                noise_count += 1
                noise_reasons[reason] += 1
                event["_filter_reason"] = reason
                fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

    elapsed = time.time() - start
    drop_pct = round(noise_count * 100 / total, 1) if total else 0
    stats = {
        "org": org,
        "source": "ms_teams",
        "input_file":  str(input_path),
        "output_file": str(output_path),
        "noise_file":  str(noise_path),
        "total_input": total,
        "kept":        kept,
        "dropped":     noise_count,
        "drop_rate_pct": drop_pct,
        "noise_reasons": dict(noise_reasons.most_common()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("FILTER COMPLETE — %s (ms_teams)", org)
    log.info("  Kept: %d / %d (%.1f%% dropped)", kept, total, drop_pct)
    for reason, count in noise_reasons.most_common():
        log.info("    %-30s %6d", reason, count)

    check_nonempty("filter", count=kept, what="Teams events kept",
                   allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# ZOHO MAIL NOISE FILTER
# ══════════════════════════════════════════════════════════════════════

_ZOHO_NOREPLY_PREFIXES = ("noreply", "no-reply", "notifications", "mailer-daemon", "bounces")
_ZOHO_AUTO_SUBJECTS = ("automatic reply", "out of office", "delivery status notification")


def is_zoho_mail_noise(event: dict) -> tuple[bool, str]:
    """Return (True, reason) if this Zoho Mail message should be dropped.

    Mirrors is_outlook_noise(): noreply/automated sender, automated subject
    keywords, empty body after HTML strip.
    """
    title = (event.get("title") or "").lower()
    body = event.get("body") or ""

    primary = (event.get("primary_actor") or "").replace("email:", "").lower()
    if "@" in primary:
        local_part, sender_domain = primary.split("@", 1)
        domain_labels = sender_domain.split(".")[:-1]

        def _matches_noreply(s: str) -> bool:
            s_norm = s.replace("-", "")
            for prefix in _ZOHO_NOREPLY_PREFIXES:
                p_norm = prefix.replace("-", "")
                if s_norm == p_norm or s_norm.startswith(p_norm):
                    return True
            return False

        if _matches_noreply(local_part) or any(_matches_noreply(lbl) for lbl in domain_labels):
            return True, "zoho_noreply_domain"

    for kw in _ZOHO_AUTO_SUBJECTS:
        if kw in title:
            return True, "zoho_auto_subject"

    effective = _HTML_TAG_RE.sub("", body).strip()
    if len(effective) < 5:
        return True, "zoho_empty_body"

    return False, ""


def run_zoho_mail_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    allow_empty: bool = False,
) -> dict:
    """Filter Zoho Mail JSONL: drop noreply senders, auto-replies, empty messages.

    Reads  output/raw/{org}_zoho_mail*.jsonl
    Writes output/filtered/{org}_zoho_mail_filtered.jsonl  (kept)
           output/raw/{org}_zoho_mail_noise.jsonl           (dropped, for audit)
    """
    from common.output_paths import ZohoPaths
    start = time.time()

    paths = ZohoPaths(org, output_dir)
    paths.makedirs()

    output_path = paths.filtered_mail_jsonl
    noise_path  = output_dir / "raw" / f"{org}_zoho_mail_noise.jsonl"

    total = kept = noise_count = 0
    noise_reasons: Counter = Counter()

    log.info("=" * 55)
    log.info("Zoho Mail Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("=" * 55)

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path,  "w", encoding="utf-8") as fnoise,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            event = json.loads(line)

            is_junk, reason = is_zoho_mail_noise(event)
            if is_junk:
                noise_count += 1
                noise_reasons[reason] += 1
                event["_filter_reason"] = reason
                fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

    elapsed = time.time() - start
    drop_pct = round(noise_count * 100 / total, 1) if total else 0
    stats = {
        "org": org,
        "source": "zoho_mail",
        "input_file":  str(input_path),
        "output_file": str(output_path),
        "noise_file":  str(noise_path),
        "total_input": total,
        "kept":        kept,
        "dropped":     noise_count,
        "drop_rate_pct": drop_pct,
        "noise_reasons": dict(noise_reasons.most_common()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("FILTER COMPLETE — %s (zoho_mail)", org)
    log.info("  Kept: %d / %d (%.1f%% dropped)", kept, total, drop_pct)
    for reason, count in noise_reasons.most_common():
        log.info("    %-30s %6d", reason, count)

    check_nonempty("filter", count=kept, what="Zoho Mail events kept",
                   allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# ZOHO CLIQ NOISE FILTER
# ══════════════════════════════════════════════════════════════════════

def is_zoho_cliq_noise(event: dict) -> tuple[bool, str]:
    """Return (True, reason) if this Cliq message should be dropped.

    Mirrors is_ms_teams_noise(): bot sender, emoji-only body, empty body.
    """
    body = event.get("body") or ""
    actors = event.get("actors") or []

    if actors and actors[0].get("is_bot"):
        return True, "zoho_cliq_bot_sender"

    effective = _HTML_TAG_RE.sub("", body).strip()

    if effective and _TEAMS_EMOJI_ONLY_RE.match(effective):
        return True, "zoho_cliq_emoji_only"

    if len(effective) < 5:
        return True, "zoho_cliq_empty_body"

    return False, ""


def run_zoho_cliq_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    allow_empty: bool = False,
) -> dict:
    """Filter Cliq JSONL: drop bot messages, emoji-only and empty messages.

    Reads  output/raw/{org}_zoho_cliq*.jsonl
    Writes output/filtered/{org}_zoho_cliq_filtered.jsonl  (kept)
           output/raw/{org}_zoho_cliq_noise.jsonl           (dropped, for audit)
    """
    from common.output_paths import ZohoPaths
    start = time.time()

    paths = ZohoPaths(org, output_dir)
    paths.makedirs()

    output_path = paths.filtered_cliq_jsonl
    noise_path  = output_dir / "raw" / f"{org}_zoho_cliq_noise.jsonl"

    total = kept = noise_count = 0
    noise_reasons: Counter = Counter()

    log.info("=" * 55)
    log.info("Zoho Cliq Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("=" * 55)

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path,  "w", encoding="utf-8") as fnoise,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            event = json.loads(line)

            is_junk, reason = is_zoho_cliq_noise(event)
            if is_junk:
                noise_count += 1
                noise_reasons[reason] += 1
                event["_filter_reason"] = reason
                fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

    elapsed = time.time() - start
    drop_pct = round(noise_count * 100 / total, 1) if total else 0
    stats = {
        "org": org,
        "source": "zoho_cliq",
        "input_file":  str(input_path),
        "output_file": str(output_path),
        "noise_file":  str(noise_path),
        "total_input": total,
        "kept":        kept,
        "dropped":     noise_count,
        "drop_rate_pct": drop_pct,
        "noise_reasons": dict(noise_reasons.most_common()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("FILTER COMPLETE — %s (zoho_cliq)", org)
    log.info("  Kept: %d / %d (%.1f%% dropped)", kept, total, drop_pct)
    for reason, count in noise_reasons.most_common():
        log.info("    %-30s %6d", reason, count)

    check_nonempty("filter", count=kept, what="Zoho Cliq events kept",
                   allow_empty=allow_empty)
    return stats


def run_jira_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    allow_empty: bool = False,
    include_archived: bool = False,
) -> dict:
    """Filter Jira JSONL: drop bots, changelogs, empty events, automated comments.

    Reads  output/raw/{org}_jira_{date}.jsonl
    Writes output/filtered/{org}_jira_filtered.jsonl  (kept events)
           output/raw/{org}_jira_noise.jsonl           (dropped, for audit)
    """
    start = time.time()

    filtered_dir = output_dir / "filtered"
    raw_dir = output_dir / "raw"
    filtered_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    # Load bot account IDs from discovery artifact so issue events (actors=[])
    # can also be checked against known app-type principals.
    from common.output_paths import JiraPaths
    artifact_path = JiraPaths(org, output_dir).scope_principals
    bot_ids = load_jira_bot_ids(artifact_path)
    if bot_ids:
        log.info("Loaded %d bot account IDs from discovery artifact", len(bot_ids))

    output_path = filtered_dir / f"{org}_jira_filtered.jsonl"
    noise_path = raw_dir / f"{org}_jira_noise.jsonl"

    total = kept = noise_count = 0
    noise_reasons: Counter = Counter()

    log.info("=" * 55)
    log.info("Jira Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("=" * 55)

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path, "w", encoding="utf-8") as fnoise,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            total += 1
            event = json.loads(line)

            is_junk, reason = is_jira_noise(event, bot_account_ids=bot_ids,
                                              include_archived=include_archived)
            if is_junk:
                noise_count += 1
                noise_reasons[reason] += 1
                event["_filter_reason"] = reason
                fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
            else:
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

    elapsed = time.time() - start
    drop_pct = round(noise_count * 100 / total, 1) if total else 0
    stats = {
        "org": org,
        "source": "jira",
        "input_file": str(input_path),
        "output_file": str(output_path),
        "noise_file": str(noise_path),
        "total_input": total,
        "kept": kept,
        "dropped": noise_count,
        "drop_rate_pct": drop_pct,
        "noise_reasons": dict(noise_reasons.most_common()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("FILTER COMPLETE — %s (jira)", org)
    log.info("  Kept: %d / %d (%.1f%% dropped)", kept, total, drop_pct)
    for reason, count in noise_reasons.most_common():
        log.info("    %-30s %6d", reason, count)

    check_nonempty("filter", count=kept, what="Jira events kept", allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# STAGE 2: CLEAN BODY TEXT (universal — works for any org)
# ══════════════════════════════════════════════════════════════════════

_LONG_URL_RE = re.compile(r"https?://\S{60,}")
_HTML_TAG_RE = re.compile(r"<[^>]{1,200}>")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{3,}")
_SIGNATURE_PATTERNS = [
    re.compile(r"^-- $", re.MULTILINE),
    re.compile(r"^(Regards|Best regards|Thanks|Cheers|Warm regards|Kind regards),?\s*$",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Sent from my (iPhone|iPad|Galaxy|Android|Samsung)",
               re.MULTILINE | re.IGNORECASE),
    re.compile(r"^Get Outlook for (iOS|Android)",
               re.MULTILINE | re.IGNORECASE),
]


def clean_body(raw_body: str) -> str:
    """Clean email body text for downstream NLP and embedding."""
    if not raw_body:
        return ""

    text = raw_body
    text = html_unescape(text)
    text = text.replace("\\n", "\n")
    text = _HTML_TAG_RE.sub("", text)
    text = _LONG_URL_RE.sub("[link]", text)

    for sig_pattern in _SIGNATURE_PATTERNS:
        match = sig_pattern.search(text)
        if match:
            candidate = text[:match.start()].strip()
            if len(candidate) >= 20:
                text = candidate
                break

    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


# ══════════════════════════════════════════════════════════════════════
# STAGE 3: CLASSIFY — LLM-powered (generalized for any org)
# ══════════════════════════════════════════════════════════════════════

_CLASSIFY_SYSTEM_PROMPT = """\
You are a classification engine for an organizational knowledge system.
You receive a batch of emails (subject + body snippet) and classify each one.

For each email, return exactly ONE type from this list:
- decision: contains an approval, rejection, agreement, sign-off, or any final call
- financial: invoices, payments, salary, billing, quotations, tax matters
- hr: leave requests, resignations, onboarding, appraisals, attendance, offers
- project_update: status updates, deployments, sprint reviews, bug reports, releases
- partnership: partner agreements, collaborations, MOUs, joint ventures
- client_communication: direct conversations with clients/customers about work
- internal_communication: internal team discussions, coordination, planning
- external_communication: external emails that don't fit other categories

Respond with ONLY a JSON array of types, one per email, in the same order.
Example: ["decision", "hr", "internal_communication"]
No explanation, no markdown, just the JSON array."""


def classify_batch_llm(
    events: list[dict],
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
) -> list[str]:
    """
    Classify a batch of emails using Claude.
    Uses Haiku for speed/cost — classification is a simple task.

    Falls back to rule-based classification on failure.
    """
    import anthropic

    snippets = []
    for e in events:
        subject = e.get("title", "(no subject)")
        body = (e.get("body", "") or "")[:300]
        sender = e.get("primary_actor", "").replace("email:", "")
        is_internal = "internal" in e.get("tags", [])
        direction = "INTERNAL" if is_internal else "EXTERNAL"
        snippets.append(f"[{direction}] From: {sender}\nSubject: {subject}\n{body}")

    user_msg = "Classify these emails:\n\n" + "\n---\n".join(
        f"Email {i+1}:\n{s}" for i, s in enumerate(snippets)
    )

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=_CLASSIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        types = json.loads(raw)

        if isinstance(types, list) and len(types) == len(events):
            return types
        else:
            log.warning("LLM returned %d types for %d events, falling back",
                       len(types) if isinstance(types, list) else 0, len(events))
            return [_classify_fallback(e) for e in events]

    except Exception as e:
        log.warning("LLM classification failed (%s), falling back to rules", e)
        return [_classify_fallback(e) for e in events]


def _classify_fallback(event: dict) -> str:
    """
    Comprehensive rule-based classification using subject + body keywords.

    Covers all 8 semantic types with pattern matching. Not as accurate as
    LLM classification but good enough for production use when no API key
    is available. Processes ~50k events/sec.
    """
    tags = event.get("tags", [])
    is_internal = "internal" in tags
    title = (event.get("title", "") or "").lower()
    body = (event.get("body", "") or "")[:500].lower()
    text = f"{title} {body}"
    sender = (event.get("primary_actor", "") or "").lower()

    # ── Decision signals ──────────────────────────────────────────
    decision_kw = [
        "approved", "rejected", "approval", "sign off", "sign-off",
        "go ahead", "confirmed", "agreed", "finalized", "sanctioned",
        "authorize", "declined", "permission granted", "greenlit",
        "thumbs up", "proceed with", "we've decided", "decision:",
        "not approved", "on hold", "let's go with",
    ]
    if any(kw in text for kw in decision_kw):
        return "decision"

    # ── Financial signals ─────────────────────────────────────────
    financial_kw = [
        "invoice", "payment", "salary", "billing", "quotation",
        "purchase order", "receipt", "tax", "gst", "tds", "pan",
        "refund", "reimbursement", "expense", "budget", "revenue",
        "profit", "loss", "balance sheet", "ledger", "credit note",
        "debit note", "bank statement", "payroll", "compensation",
        "amount due", "outstanding", "receivable", "payable",
        "challan", "itr", "gstr", "e-way bill", "eway bill",
    ]
    if any(kw in text for kw in financial_kw):
        return "financial"

    # ── HR signals ────────────────────────────────────────────────
    hr_kw = [
        "leave", "resignation", "onboarding", "appraisal",
        "attendance", "offer letter", "joining", "notice period",
        "performance review", "increment", "promotion", "transfer",
        "probation", "training", "workshop", "team outing",
        "birthday", "anniversary", "policy update", "handbook",
        "wfh", "work from home", "half day", "sick leave",
        "casual leave", "earned leave", "holiday", "annual leave",
        "maternity", "paternity", "exit interview", "full and final",
        "relieving", "experience letter", "reference letter",
        "job description", "vacancy", "recruitment", "interview",
        "candidate", "shortlist", "hire", "hiring",
    ]
    # Also check if sender is HR
    is_hr_sender = any(x in sender for x in ["hr@", "hr1@", "archita@"])
    if any(kw in text for kw in hr_kw) or (is_hr_sender and is_internal):
        return "hr"

    # ── Project update signals ────────────────────────────────────
    project_kw = [
        "deploy", "deployment", "release", "sprint", "bug",
        "feature", "milestone", "update:", "status update",
        "progress", "standup", "stand-up", "retro", "retrospective",
        "code review", "pull request", "merge", "commit",
        "jira", "clickup", "trello", "ticket", "task",
        "production", "staging", "server", "api", "database",
        "migration", "downtime", "outage", "performance",
        "testing", "qa", "test case", "build", "pipeline",
        "launch", "go live", "go-live", "beta", "alpha",
        "version", "patch", "hotfix", "rollback",
    ]
    if any(kw in text for kw in project_kw):
        return "project_update"

    # ── Partnership signals ───────────────────────────────────────
    partnership_kw = [
        "partnership", "collaboration", "mou", "memorandum",
        "joint venture", "strategic alliance", "reseller",
        "distributor", "affiliate", "channel partner",
        "co-brand", "cobrand", "white label", "integration partner",
        "referral agreement", "commission structure",
    ]
    if any(kw in text for kw in partnership_kw):
        return "partnership"

    # ── Client communication ──────────────────────────────────────
    client_kw = [
        "client", "customer", "requirement", "deliverable",
        "scope", "timeline", "demo", "presentation",
        "proposal", "quotation", "feedback", "complaint",
        "support ticket", "escalation", "onboard",
        "contract", "agreement", "sla", "sow",
        "renewal", "subscription", "license",
    ]
    if not is_internal and any(kw in text for kw in client_kw):
        return "client_communication"

    # ── Default: internal vs external ─────────────────────────────
    if is_internal:
        return "internal_communication"
    else:
        return "external_communication"


# ══════════════════════════════════════════════════════════════════════
# REGENERATE EMBEDDING TEXT
# ══════════════════════════════════════════════════════════════════════

def make_embedding_text(event: dict) -> str:
    """Produces improved embedding text with semantic type and cleaned body."""
    semantic_type = event.get("semantic_type", "email")
    lines = [
        f"[{semantic_type.upper().replace('_', ' ')}]",
        f"Date: {event.get('timestamp', '')[:10]}",
        f"From: {event.get('primary_actor', '').replace('email:', '')}",
    ]

    actors = event.get("actors", [])
    recipients = [a.get("email", a.get("id", "")) for a in actors
                  if a.get("id") != event.get("primary_actor")]
    if recipients:
        lines.append(f"To: {', '.join(recipients[:5])}")

    lines.append(f"Subject: {event.get('title', '')}")

    body = event.get("body", "")
    if body:
        lines.append(f"\n{body[:2000]}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════

def run_filter(
    input_path: Path,
    output_dir: Path,
    org: str,
    min_body_length: int = 20,
    keep_empty_body: bool = False,
    use_llm: bool = True,
    llm_batch_size: int = 20,
    discover_noise: bool = False,
    allow_empty: bool = False,
) -> dict:
    """
    Run the full filter pipeline on a raw JSONL file.
    Works for any org — uses org config for org-specific noise domains.

    Returns stats dict with counts and timing.
    """
    start = time.time()

    # Resolve subdirectories
    config_dir = output_dir / "config"
    filtered_dir = output_dir / "filtered"
    raw_dir = output_dir / "raw"
    for d in (config_dir, filtered_dir, raw_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Load org config
    org_config = load_org_config(org, config_dir)
    org_domains = org_config.get("org_domains", [])

    # Auto-discover noise domains if requested
    if discover_noise and org_domains:
        discovered = discover_noise_domains(input_path, org_domains)
        existing = set(org_config.get("discovered_noise_domains", []))
        new_domains = [d["domain"] for d in discovered]
        existing.update(new_domains)
        org_config["discovered_noise_domains"] = sorted(existing)
        save_org_config(org_config, config_dir)
        log.info("Updated org config with %d discovered noise domains", len(new_domains))

    # Build combined noise domain set
    org_noise = set(org_config.get("discovered_noise_domains", []))
    org_allowlisted = set(org_config.get("allowlisted_domains", []))

    # Check for API key if LLM classification is enabled
    llm_requested = use_llm
    llm_fallback_reason: str | None = None
    api_key = get_settings().anthropic_api_key
    if use_llm and not api_key:
        log.warning("ANTHROPIC_API_KEY not set — falling back to rule-based classification")
        llm_fallback_reason = "missing_anthropic_api_key"
        use_llm = False

    # Output files
    output_path = filtered_dir / f"{org}_gmail_filtered.jsonl"
    noise_path = raw_dir / f"{org}_gmail_noise.jsonl"
    stats_path = config_dir / f"{org}_gmail_filter_stats.json"

    # Counters
    total = 0
    kept = 0
    noise_count = 0
    noise_reasons = Counter()
    semantic_types = Counter()
    body_stats = {"had_body": 0, "cleaned_empty": 0, "no_body": 0}

    log.info("=" * 55)
    log.info("Gmail Filter Pipeline — %s", org)
    log.info("Input:  %s", input_path)
    log.info("Output: %s", output_path)
    log.info("Classification: %s", "LLM (Claude)" if use_llm else "fallback (rule-based)")
    log.info("Org noise domains: %d (universal) + %d (discovered)",
             len(UNIVERSAL_NOISE_DOMAINS), len(org_noise))
    log.info("=" * 55)

    # Read, filter, clean — then batch-classify
    pending_classification: list[dict] = []

    with (
        open(input_path, "r", encoding="utf-8") as fin,
        open(output_path, "w", encoding="utf-8") as fout,
        open(noise_path, "w", encoding="utf-8") as fnoise,
    ):
        def _flush_batch(batch: list[dict]):
            """Classify and write a batch of events."""
            nonlocal kept
            if not batch:
                return

            if use_llm:
                types = classify_batch_llm(batch, api_key)
            else:
                types = [_classify_fallback(e) for e in batch]

            for event, stype in zip(batch, types):
                event["semantic_type"] = stype
                semantic_types[stype] += 1
                event["embedding_text"] = make_embedding_text(event)
                fout.write(json.dumps(event, ensure_ascii=False) + "\n")
                kept += 1

        for line in fin:
            total += 1
            event = json.loads(line)
            source = (event.get("source") or "").lower()
            is_slack_event = source == "slack"

            # ── Stage 1: Filter ──────────────────────────────────────
            if not is_slack_event:
                is_junk, reason = is_noise(event, org_noise, org_allowlisted)
                if is_junk:
                    noise_count += 1
                    noise_reasons[reason] += 1
                    event["_filter_reason"] = reason
                    fnoise.write(json.dumps(event, ensure_ascii=False) + "\n")
                    continue

            # ── Stage 2: Clean body ──────────────────────────────────
            raw_body = event.get("body", "")
            if raw_body:
                body_stats["had_body"] += 1
                cleaned = clean_body(raw_body)
                if not cleaned or len(cleaned) < min_body_length:
                    body_stats["cleaned_empty"] += 1
                    # Slack events must bypass Gmail-specific drop behavior.
                    if not keep_empty_body and not is_slack_event:
                        noise_count += 1
                        noise_reasons["empty_after_clean"] += 1
                        continue
                event["body"] = cleaned
            else:
                body_stats["no_body"] += 1

            # ── Accumulate for batch classification ───────────────────
            pending_classification.append(event)
            if len(pending_classification) >= llm_batch_size:
                _flush_batch(pending_classification)
                pending_classification = []

            # Progress log
            if total % 50000 == 0:
                log.info("  ...processed %d events (kept %d, dropped %d)",
                         total, kept, noise_count)

        # Flush remaining
        _flush_batch(pending_classification)

    elapsed = time.time() - start

    # ── Stats ────────────────────────────────────────────────────────
    stats = {
        "org": org,
        "input_file": str(input_path),
        "output_file": str(output_path),
        "noise_file": str(noise_path),
        "total_input": total,
        "kept": kept,
        "dropped": noise_count,
        "drop_rate_pct": round(noise_count * 100 / total, 1) if total else 0,
        "noise_reasons": dict(noise_reasons.most_common()),
        "semantic_types": dict(semantic_types.most_common()),
        "body_stats": body_stats,
        "llm_requested": llm_requested,
        "llm_fallback_reason": llm_fallback_reason,
        "classification_mode": "llm" if use_llm else "fallback",
        "noise_domains_universal": len(UNIVERSAL_NOISE_DOMAINS),
        "noise_domains_discovered": len(org_noise),
        "duration_seconds": round(elapsed, 1),
    }

    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    log.info("")
    log.info("=" * 55)
    log.info("FILTER COMPLETE — %s", org)
    log.info("  Input events   : %d", total)
    log.info("  Kept (clean)   : %d (%.1f%%)", kept, kept * 100 / total if total else 0)
    log.info("  Dropped (noise): %d (%.1f%%)", noise_count, noise_count * 100 / total if total else 0)
    log.info("")
    log.info("  Noise breakdown:")
    for reason, count in noise_reasons.most_common():
        log.info("    %-25s %6d", reason, count)
    log.info("")
    log.info("  Semantic types:")
    for stype, count in semantic_types.most_common():
        log.info("    %-25s %6d", stype, count)
    log.info("")
    log.info("  Duration: %.1fs", elapsed)
    log.info("  Output:   %s", output_path)
    log.info("=" * 55)

    log_summary(
        "filter",
        org=org,
        input=total,
        kept=kept,
        dropped=noise_count,
        drop_pct=stats["drop_rate_pct"],
        classification=stats["classification_mode"],
        llm_requested=llm_requested,
        llm_fallback=llm_fallback_reason,
        duration_s=round(elapsed, 1),
    )
    check_nonempty("filter", count=kept, what="events kept",
                   allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Filter raw KnowledgeEvent JSONL (Gmail or Jira)")
    parser.add_argument("--input", required=True, help="Path to raw JSONL file")
    parser.add_argument("--org", required=True, help="Organization name (e.g. ezobooks)")
    parser.add_argument("--source", choices=["gmail", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"],
                        default=None,
                        help="Source type (auto-detected from filename if omitted)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: parent of raw/)")
    parser.add_argument("--min-body", type=int, default=20,
                        help="Minimum body length after cleaning (default: 20, Gmail only)")
    parser.add_argument("--keep-empty", action="store_true",
                        help="Keep events with empty body (Gmail only)")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM classification, use rule-based fallback (Gmail only)")
    parser.add_argument("--discover-noise", action="store_true",
                        help="Run auto-discovery for org-specific noise domains (Gmail only)")
    parser.add_argument("--llm-batch-size", type=int, default=20,
                        help="Emails per LLM classification call (default: 20, Gmail only)")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if the stage produces 0 kept events.")

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent.parent  # parent of raw/

    # Auto-detect source from filename if not explicitly given
    source = args.source
    if source is None:
        name = input_path.name
        if "_jira_" in name:
            source = "jira"
        elif "_outlook_mail" in name or "_outlook_" in name:
            source = "outlook_mail"
        elif "_ms_teams" in name:
            source = "ms_teams"
        elif "_zoho_mail" in name:
            source = "zoho_mail"
        elif "_zoho_cliq" in name:
            source = "zoho_cliq"
        else:
            source = "gmail"

    from common.logger import setup_logging
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(logs_dir), source=f"{source}_filter")

    if source == "jira":
        run_jira_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            allow_empty=args.allow_empty,
        )
    elif source == "outlook_mail":
        run_outlook_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            allow_empty=args.allow_empty,
        )
    elif source == "ms_teams":
        run_ms_teams_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            allow_empty=args.allow_empty,
        )
    elif source == "zoho_mail":
        run_zoho_mail_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            allow_empty=args.allow_empty,
        )
    elif source == "zoho_cliq":
        run_zoho_cliq_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            allow_empty=args.allow_empty,
        )
    else:
        input_path = latest_nonempty_path(args.input, label="Filter input", logger=log)
        run_filter(
            input_path=input_path,
            output_dir=output_dir,
            org=args.org,
            min_body_length=args.min_body,
            keep_empty_body=args.keep_empty,
            use_llm=not args.skip_llm,
            llm_batch_size=args.llm_batch_size,
            discover_noise=args.discover_noise,
            allow_empty=args.allow_empty,
        )


if __name__ == "__main__":
    main()
