"""
common/email_canon.py
─────────────────────
Provider-scoped email canonicalization — the load-bearing helper for
Tier-1 identity joins (Identity_resolution.md §4.3).

Two addresses that are *the same mailbox* must canonicalize to one string so
the alias table joins them; two addresses that are *different mailboxes* must
NOT be collapsed. The rule that keeps us safe: only apply provider-specific
normalization (gmail dot-collapse, +tag stripping) to KNOWN provider domains.
Applying gmail's dot rule to a corporate tenant that treats dots as significant
would silently merge two real people — over-merging is worse than missing a merge.

Hard invariant: canonical_email(canonical_email(x)) == canonical_email(x).

This does NOT parse messy actor text (`"Ravi" <ravi@x>`); that is `clean_email()`
in person_registry. This takes an already-extracted address and normalizes it.
"""

from __future__ import annotations

import re

# Address hygiene. After splitting on "@", a valid local part / domain must fully match
# these. A stray wrapping char (`"shri@…`, `<rahul@…>`) is stripped from the ENDS first
# (the common case); anything still illegal in the middle fails the fullmatch and the whole
# address is rejected as "" — better no key than a garbage identity key.
_WRAP_JUNK = "\"'<>(),;:` \t\r\n"
_LOCAL_OK = re.compile(r"[a-z0-9._%+\-]+")
_DOMAIN_OK = re.compile(r"[a-z0-9.\-]+")

# Provider rules. `strip_plus` = drop "+tag"; `strip_dots` = remove dots in the
# local part; `alias_of` = rewrite the domain to a canonical one.
PROVIDER_RULES: dict[str, dict] = {
    "gmail.com":      {"strip_plus": True,  "strip_dots": True,  "alias_of": None},
    "googlemail.com": {"strip_plus": True,  "strip_dots": True,  "alias_of": "gmail.com"},
    "outlook.com":    {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "hotmail.com":    {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "live.com":       {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "msn.com":        {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "icloud.com":     {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "me.com":         {"strip_plus": True,  "strip_dots": False, "alias_of": None},
    "mac.com":        {"strip_plus": True,  "strip_dots": False, "alias_of": None},
}

# Yahoo uses "-tag" sub-addressing instead of "+tag".
_YAHOO_DOMAINS = {"yahoo.com", "yahoo.co.in", "yahoo.co.uk", "ymail.com", "rocketmail.com"}


def canonical_email(addr: str | None) -> str:
    """Normalize a single email address to its canonical form.

    Returns "" when the input has no usable domain (caller treats as "no email").
    Idempotent by construction.
    """
    if not addr:
        return ""
    local, sep, domain = addr.strip().lower().strip(".").partition("@")
    # Strip leftover wrapping junk from both sides of each half (`"shri`, `ezobooks.in>`).
    local = local.strip(_WRAP_JUNK)
    domain = domain.strip(_WRAP_JUNK).strip(".")
    if not sep or not domain or not local:
        return ""

    # IDN → punycode so unicode and ascii spellings of a domain agree (before validation,
    # so a legitimate unicode domain becomes ascii and passes _DOMAIN_OK).
    try:
        domain = domain.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        pass  # leave as-is; lowercase already applied

    # Reject anything still malformed (junk surviving in the middle, spaces, two @, …).
    if not _LOCAL_OK.fullmatch(local) or not _DOMAIN_OK.fullmatch(domain) or "." not in domain:
        return ""

    if domain in _YAHOO_DOMAINS:
        local = local.split("-", 1)[0]
        return f"{local}@{domain}"

    rules = PROVIDER_RULES.get(domain)
    if rules:
        if rules["strip_plus"]:
            local = local.split("+", 1)[0]
        if rules["strip_dots"]:
            local = local.replace(".", "")
        if rules["alias_of"]:
            domain = rules["alias_of"]

    # Unknown / org domains: lowercase only (already done). Never guess +/dot behaviour.
    return f"{local}@{domain}" if local else ""


def email_alias_key(addr: str | None) -> str:
    """Alias-table key for an email, or "" if the address is unusable.

    Key namespace `email:` matches Identity_resolution.md §1.2.
    """
    canon = canonical_email(addr)
    return f"email:{canon}" if canon else ""
