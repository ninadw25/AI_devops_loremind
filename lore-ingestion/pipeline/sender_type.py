"""
pipeline/sender_type.py
───────────────────────
Rule-based sender-type classification — the F3 fix from
`docs/revised_ingestion_pipeline/00_DESIGN.md` §7 and G3 in
`01_LLM_GUARDRAILS.md`.

Labels each identity as human / system / newsletter / external_marketing /
functional_mailbox using headers + behavior, NO LLM (design §8: "Sender
classification = rules"). Runs BEFORE Person creation so non-humans never
enter the person alias graph — killing the `rupesh garg ×9`, `atlassian ×16`,
`mail delivery system ×6` pollution and the newsletter facts that leaked into
knowledge.

This module is the single consolidated home for the noreply / functional
patterns that were previously copied across filter.py, person_registry.py and
gmail/fetcher.py. It is pure (no IO) and mirrors the shape of common/rbac.py.

Run the self-check:  ../.venv/bin/python -m pipeline.sender_type
"""

from __future__ import annotations

from enum import Enum

# Reuse — filter.py owns the curated universal noise-domain list; it imports
# only from common/, so there is no import cycle.
from pipeline.filter import UNIVERSAL_NOISE_DOMAINS


class SenderType(str, Enum):
    """str-Enum → serializes to JSON as the bare string; == "human" works."""
    HUMAN = "human"
    SYSTEM = "system"                       # noreply, mailer-daemon, bots, notifications
    NEWSLETTER = "newsletter"               # bulk / List-Unsubscribe / promo senders
    EXTERNAL_MARKETING = "external_marketing"  # cold outreach / spam personas
    FUNCTIONAL_MAILBOX = "functional_mailbox"  # hr@, accounts@ — real org knowledge


# Threads made up entirely of these never reach the extractor (G3). Note
# FUNCTIONAL_MAILBOX is deliberately EXCLUDED — hr@/accounts@ carry real
# organizational knowledge and must still be extracted.
SKIP_TYPES = {SenderType.SYSTEM.value, SenderType.NEWSLETTER.value,
              SenderType.EXTERNAL_MARKETING.value}

# ── Consolidated constants (single source; previously duplicated 3×) ─────────

# Automated / never-human local parts and domain labels. Lifted from
# filter.py:_OUTLOOK_NOREPLY_PREFIXES and extended with bounce/postmaster set.
NOREPLY_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications", "notification",
    "mailer-daemon", "mailerdaemon", "postmaster", "daemon",
    "bounce", "bounces", "dmarc", "abuse",
    "auto-reply", "autoreply",
)

# Functional-mailbox local parts. Consolidated from
# person_registry.FUNCTIONAL_MAILBOX_PREFIXES + FUNCTIONAL_NAMES.
FUNCTIONAL_PREFIXES = {
    "account", "accounts", "ai", "dev", "dm", "hr", "influencer", "manager",
    "partner", "sales", "support", "info", "admin", "billing",
    "marketing", "contact", "hello", "team", "ops", "operations",
    "finance", "legal", "reports", "careers", "jobs", "hello", "help",
}

# Gmail category labels that mark bulk/promotional mail.
PROMO_LABELS = {"CATEGORY_PROMOTIONS", "CATEGORY_FORUMS", "CATEGORY_UPDATES"}

# R5 thresholds — the one soft heuristic. Conservative on purpose (a false
# EXTERNAL_MARKETING label only affects analytics, never ACLs).
# ponytail: constants, not org_config knobs, until an org actually needs tuning.
_EXT_MIN_SENT = 3
_EXT_MAX_CONTACTS = 2


def _matches_noreply(s: str) -> bool:
    """True if s equals or starts with any noreply prefix (hyphen-normalized)."""
    s_norm = s.replace("-", "")
    for prefix in NOREPLY_PREFIXES:
        p_norm = prefix.replace("-", "")
        if s_norm == p_norm or s_norm.startswith(p_norm):
            return True
    return False


def is_functional_local(local: str, extra_functional: set[str] | None = None) -> bool:
    """Functional-mailbox check by local part (hr, accounts, support…)."""
    local = local.lower()
    if not local:
        return False
    if extra_functional and local in extra_functional:
        return True
    if local in FUNCTIONAL_PREFIXES:
        return True
    segments = set(local.replace(".", " ").replace("-", " ").replace("_", " ").split())
    return bool(segments & FUNCTIONAL_PREFIXES)


def classify_sender(f: dict) -> tuple[SenderType, str]:
    """Classify one identity from precomputed features. First match wins.

    Feature keys (all cheap, all available in person_registry's existing scan):
        local_part, sender_domain, is_org_domain, is_personal_domain,
        is_bot, had_list_unsubscribe, precedence_bulk, promo_label,
        in_noise_domain, is_allowlisted, extra_functional (set),
        emails_sent, emails_received, unique_contacts

    Returns (SenderType, reason) — reason is a short stable slug for logging
    (the label distribution is a content-free quality metric, design 02 §9.2).
    """
    local = f.get("local_part", "")

    # R0 — bot/app actor (source-authoritative, highest precision)
    if f.get("is_bot"):
        return SenderType.SYSTEM, "bot_actor"

    # R1 — automated local-part / domain label (noreply, mailer-daemon…)
    if _matches_noreply(local) or any(
        _matches_noreply(lbl) for lbl in f.get("sender_domain", "").split(".")[:-1]
    ):
        return SenderType.SYSTEM, "noreply_pattern"

    # R2 — bulk / newsletter markers (RFC-2369 List-Unsubscribe is definitional)
    if f.get("had_list_unsubscribe") or f.get("precedence_bulk") or f.get("promo_label"):
        return SenderType.NEWSLETTER, "bulk_marker"

    # R3 — curated noise domain (backstop), unless org-allowlisted
    if f.get("in_noise_domain") and not f.get("is_allowlisted"):
        return SenderType.SYSTEM, "noise_domain"

    # R4 — functional mailbox (real org knowledge; fires AFTER system rules so
    # noreply@ / marketing@ are typed system/newsletter, not functional)
    if is_functional_local(local, f.get("extra_functional")):
        return SenderType.FUNCTIONAL_MAILBOX, "functional_local"

    # R5 — external send-only outreach (the one soft heuristic; conservative)
    if (not f.get("is_org_domain") and not f.get("is_personal_domain")
            and f.get("emails_received", 0) == 0
            and f.get("emails_sent", 0) >= _EXT_MIN_SENT
            and f.get("unique_contacts", 0) <= _EXT_MAX_CONTACTS):
        return SenderType.EXTERNAL_MARKETING, "send_only_outreach"

    # R6 — default
    return SenderType.HUMAN, "default"


def build_features(
    primary_email: str,
    *,
    is_org_domain: bool,
    is_bot: bool = False,
    had_list_unsubscribe: bool = False,
    precedence_bulk: bool = False,
    promo_label: bool = False,
    extra_functional: set[str] | None = None,
    noise_domains: set[str] | None = None,
    allowlisted_domains: set[str] | None = None,
    personal_domains: set[str] | None = None,
    emails_sent: int = 0,
    emails_received: int = 0,
    unique_contacts: int = 0,
) -> dict:
    """Assemble the classify_sender feature dict from raw ingredients."""
    local, _, domain = primary_email.partition("@")
    domain = domain.lower()
    parent = ".".join(domain.split(".")[-2:]) if domain.count(".") >= 1 else domain
    noise = noise_domains or set()
    return {
        "local_part": local.lower(),
        "sender_domain": domain,
        "is_org_domain": is_org_domain,
        "is_personal_domain": bool(personal_domains and domain in personal_domains),
        "is_bot": is_bot,
        "had_list_unsubscribe": had_list_unsubscribe,
        "precedence_bulk": precedence_bulk,
        "promo_label": promo_label,
        "in_noise_domain": domain in noise or domain in UNIVERSAL_NOISE_DOMAINS
                           or parent in noise or parent in UNIVERSAL_NOISE_DOMAINS,
        "is_allowlisted": bool(allowlisted_domains and domain in allowlisted_domains),
        "extra_functional": extra_functional or set(),
        "emails_sent": emails_sent,
        "emails_received": emails_received,
        "unique_contacts": unique_contacts,
    }


def thread_is_all_nonhuman(participant_emails, sender_type_map: dict) -> bool:
    """G3: True iff every participant is a skippable non-human sender.

    Functional mailboxes count as human here (they carry org knowledge), so a
    thread touching hr@ is NOT skipped.
    """
    types = [sender_type_map.get(e.lower(), "human") for e in participant_emails]
    return bool(types) and all(t in SKIP_TYPES for t in types)


def demo() -> None:
    P = {"gmail.com", "yahoo.com", "outlook.com"}
    cases = [
        (dict(local_part="ravi", sender_domain="ezobooks.in", is_org_domain=True,
              emails_sent=40, emails_received=55, unique_contacts=18), SenderType.HUMAN),
        (dict(is_bot=True, local_part="jira", sender_domain="atlassian.net"), SenderType.SYSTEM),
        (dict(local_part="noreply", sender_domain="atlassian.com", is_org_domain=False,
              emails_sent=16, emails_received=0, unique_contacts=1), SenderType.SYSTEM),
        (dict(local_part="mailer-daemon", sender_domain="googlemail.com",
              emails_sent=6, emails_received=0, unique_contacts=1), SenderType.SYSTEM),
        (dict(local_part="news", sender_domain="substack.com", had_list_unsubscribe=True,
              promo_label=True, emails_sent=30, emails_received=0, unique_contacts=1),
         SenderType.NEWSLETTER),
        (dict(local_part="hr", sender_domain="ezobooks.in", is_org_domain=True,
              emails_sent=25, emails_received=12, unique_contacts=9),
         SenderType.FUNCTIONAL_MAILBOX),
        (dict(local_part="rupesh.garg", sender_domain="throwaway-vendor.biz",
              is_org_domain=False, is_personal_domain=False,
              emails_sent=5, emails_received=0, unique_contacts=1),
         SenderType.EXTERNAL_MARKETING),
    ]
    for feats, expected in cases:
        got, reason = classify_sender(feats)
        assert got == expected, f"{feats}: expected {expected}, got {got} ({reason})"

    # build_features integration + personal-domain path
    f = build_features("someone@gmail.com", is_org_domain=False, personal_domains=P,
                       emails_sent=2, emails_received=8, unique_contacts=5)
    assert classify_sender(f)[0] == SenderType.HUMAN
    assert f["is_personal_domain"] is True

    # G3 helper: all-nonhuman skips; one human or one functional does not
    m = {"noreply@atlassian.com": "system", "news@substack.com": "newsletter",
         "ravi@ezobooks.in": "human", "hr@ezobooks.in": "functional_mailbox"}
    assert thread_is_all_nonhuman(["noreply@atlassian.com", "news@substack.com"], m) is True
    assert thread_is_all_nonhuman(["ravi@ezobooks.in", "noreply@atlassian.com"], m) is False
    assert thread_is_all_nonhuman(["hr@ezobooks.in"], m) is False  # functional not skipped
    print("sender_type demo: all assertions passed")


if __name__ == "__main__":
    demo()
