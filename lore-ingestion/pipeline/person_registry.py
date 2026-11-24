"""
pipeline/person_registry.py
────────────────────────────
Builds a canonical person registry from ingested KnowledgeEvents.

Resolves multiple email addresses and display name variants into
a single canonical person identity. Works for ANY organization.

Identity resolution strategy:
  1. Collect all (email, display_name) pairs from event actors
  2. Clean emails (strip malformed angle brackets, quotes, whitespace)
  3. Cluster org-domain emails by local part (rishikesh@domain1 = rishikesh@domain2)
  4. Select best canonical name per cluster (most frequent proper name)
  5. Compute communication stats per person
  6. Output person registry as JSON

Usage:
  python -m common.person_registry --input output/org_gmail_filtered.jsonl --org myorg
"""

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from common import scopes
from common.logger import get_logger
from common.org_config import load_org_config
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from pipeline.sender_type import (
    PROMO_LABELS, SenderType, build_features, classify_sender,
)

log = get_logger("common.person_registry")


# ══════════════════════════════════════════════════════════════════════
# EMAIL CLEANING
# ══════════════════════════════════════════════════════════════════════

# Shared functional mailbox prefixes — same set used in gmail/fetcher.py
FUNCTIONAL_MAILBOX_PREFIXES = {
    "account", "accounts", "ai", "dev", "dm", "hr", "influencer", "manager",
    "partner", "sales", "support", "info", "admin", "billing",
    "noreply", "no-reply", "marketing", "contact", "hello",
    "team", "ops", "operations", "finance", "legal", "reports",
}


def _is_functional_email(email: str, extra_functional: set[str] | None = None) -> bool:
    """Check if an email is a shared/functional mailbox by local part."""
    local = email.split("@")[0].lower() if "@" in email else ""
    if not local:
        return False
    if extra_functional and local in extra_functional:
        return True
    if local in FUNCTIONAL_MAILBOX_PREFIXES:
        return True
    segments = set(local.replace(".", " ").replace("-", " ").replace("_", " ").split())
    return bool(segments & FUNCTIONAL_MAILBOX_PREFIXES)


_ANGLE_BRACKET_RE = re.compile(r"<([^>]+)>")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def clean_email(raw: str) -> str:
    """
    Clean a potentially malformed email address.

    Handles:
      - 'hr@ezobooks.in>" <hr@ezobooks.in' → 'hr@ezobooks.in'
      - '"gk@ezobanks.com"' → 'gk@ezobanks.com'
      - 'email:user@domain.com' → 'user@domain.com'
    """
    if not raw:
        return ""

    # Strip email: prefix (from actor IDs)
    text = raw.replace("email:", "").strip()

    # Try extracting from angle brackets first
    match = _ANGLE_BRACKET_RE.search(text)
    if match:
        text = match.group(1).strip()

    # If still looks malformed (contains >, <, spaces), extract first valid email
    if ">" in text or "<" in text or " " in text:
        email_match = _EMAIL_RE.search(text)
        if email_match:
            return email_match.group(0).lower()
        return ""

    # Must contain @ to be valid
    if "@" not in text:
        return ""

    return text.lower().strip()


# ══════════════════════════════════════════════════════════════════════
# NAME CLEANING & SELECTION
# ══════════════════════════════════════════════════════════════════════

# Names that are clearly not real person names
_GARBAGE_NAME_PATTERNS = [
    re.compile(r"^.{0,1}$"),                     # empty or single char
    re.compile(r"@"),                              # email address used as name
    re.compile(r"^['\"]"),                         # starts with quote
    re.compile(r"^(me|sender|there|hr|accounts|admin|info|contact|team|ops|legal|finance|billing)$", re.I),
    re.compile(r"^(hr team|sales team|support team|dev team|marketing team|accounts team)$", re.I),
    re.compile(r"\b(private|limited|pvt|ltd|technologies|pvt\.|llp)\b", re.I),
    re.compile(r"^[\-\.\s]+$"),                   # only punctuation
    re.compile(r"[^\x00-\x7F]", re.I),            # non-ASCII (chinese chars, etc.)
    re.compile(r"^(cc|bcc|to|from):?\s", re.I),   # email header prefixes
    re.compile(r"(figma|via google|via indiamart|microsoft outlook)", re.I),
    re.compile(r"not provided", re.I),
    re.compile(r"(iphone|android|samsung)", re.I),
]


def _is_garbage_name(name: str) -> bool:
    """Check if a display name is garbage / not a real person name."""
    if not name or len(name.strip()) <= 1:
        return True
    for pattern in _GARBAGE_NAME_PATTERNS:
        if pattern.search(name.strip()):
            return True
    return False


def _name_quality_score(name: str) -> int:
    """
    Score a display name by quality. Higher = better candidate for canonical name.

    Preferences:
      - Proper case "Rahul Sharma" > all-lower "rahul sharma"
      - Two-word names > single word > three+ words
      - No special chars > with special chars
    """
    name = name.strip()
    score = 0

    # Proper title case (first letter capitalized)
    words = name.split()
    if all(w[0].isupper() for w in words if w):
        score += 10

    # Two-word names are ideal (first + last)
    if len(words) == 2:
        score += 5
    elif len(words) == 1:
        score += 2
    elif len(words) == 3:
        score += 3

    # Penalize ALL CAPS
    if name == name.upper() and len(name) > 3:
        score -= 3

    # Penalize names with special chars
    if re.search(r"[^a-zA-Z\s\.\-']", name):
        score -= 2

    return score


def select_canonical_name(name_counts: Counter) -> str:
    """
    From a Counter of display names, select the best canonical name.

    Strategy:
      1. Filter out garbage names
      2. Score remaining by quality
      3. Break ties by frequency
    """
    candidates = []
    for name, count in name_counts.items():
        if _is_garbage_name(name):
            continue
        quality = _name_quality_score(name)
        candidates.append((name, quality, count))

    if not candidates:
        # Fallback: just return the most frequent non-empty name
        for name, count in name_counts.most_common():
            if name and len(name.strip()) > 1:
                return name.strip()
        return ""

    # Sort by quality (desc) then frequency (desc)
    candidates.sort(key=lambda x: (-x[1], -x[2]))
    return candidates[0][0].strip()


# ══════════════════════════════════════════════════════════════════════
# PERSON REGISTRY BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_person_registry(
    input_path: Path,
    org_domains: list[str],
    functional_emails: set[str] | None = None,
    noise_domains: set[str] | None = None,
    allowlisted_domains: set[str] | None = None,
) -> dict:
    """
    Build a person registry from a JSONL events file.

    Returns dict with:
      - persons: list of person records
      - email_to_person_id: lookup map
      - stats: summary statistics
    """
    log.info("Building person registry from %s", input_path)
    start = time.time()

    org_domain_set = set(d.lower() for d in org_domains)
    functional_emails = functional_emails or set()

    # ── Phase 1: Collect all email → display_name pairs ──────────────
    email_names: dict[str, Counter] = defaultdict(Counter)
    email_sent: Counter = Counter()       # times this email appears as sender
    email_received: Counter = Counter()   # times this email appears as recipient
    email_threads: dict[str, set] = defaultdict(set)
    email_contacts: dict[str, set] = defaultdict(set)
    # Sender-type signals (F3 fix) — attributed to the message's sender.
    email_list_unsub: set = set()   # sender ever sent a List-Unsubscribe message
    email_promo: set = set()        # sender ever sent a promo/forum-labeled message
    email_is_bot: set = set()       # actor flagged is_bot by a source normalizer

    event_count = 0
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            event_count += 1

            sender_raw = event.get("primary_actor", "")
            sender = clean_email(sender_raw)
            actors = event.get("actors", [])
            thread_id = event.get("parent_id", "") or event.get("source_id", "")
            raw = event.get("raw") or {}

            # Track sender + per-message bulk signals (attributed to sender)
            if sender:
                email_sent[sender] += 1
                if thread_id:
                    email_threads[sender].add(thread_id)
                if raw.get("list_unsubscribe"):
                    email_list_unsub.add(sender)
                if set(raw.get("label_ids") or ()) & PROMO_LABELS:
                    email_promo.add(sender)

            # Track all actors — clean emails to normalize malformed addresses
            all_emails_in_event = set()
            for actor in actors:
                raw_email = actor.get("email", "") or actor.get("emailAddress", "") or ""
                raw_id = actor.get("id", "") or actor.get("accountId", "") or ""
                email = clean_email(raw_email) or clean_email(raw_id)
                name = actor.get("display_name", "") or actor.get("displayName", "")
                if not email:
                    continue

                email_names[email][name] += 1
                all_emails_in_event.add(email)
                if actor.get("is_bot") or actor.get("accountType") == "app":
                    email_is_bot.add(email)

                if email != sender:
                    email_received[email] += 1
                if thread_id:
                    email_threads[email].add(thread_id)

            # Track contacts (who talks to whom)
            for email in all_emails_in_event:
                email_contacts[email].update(all_emails_in_event - {email})

    log.info("Phase 1: Scanned %d events, found %d unique emails", event_count, len(email_names))

    # ── Phase 2: Cluster emails by person ────────────────────────────
    # For org-domain emails, same local part = same person
    local_to_org_emails: dict[str, set] = defaultdict(set)
    for email in email_names:
        domain = email.split("@")[-1] if "@" in email else ""
        if domain in org_domain_set:
            local = email.split("@")[0]
            local_to_org_emails[local].add(email)

    # Build clusters: each cluster is a set of emails belonging to the same person
    email_to_cluster_id: dict[str, int] = {}
    clusters: dict[int, set] = {}
    next_cluster_id = 0

    # First: merge org-domain aliases
    for local, emails in local_to_org_emails.items():
        # Check if any of these emails already have a cluster
        existing_ids = {email_to_cluster_id[e] for e in emails if e in email_to_cluster_id}

        if existing_ids:
            # Merge into the lowest existing cluster
            target_id = min(existing_ids)
            for cid in existing_ids:
                if cid != target_id:
                    for e in clusters[cid]:
                        email_to_cluster_id[e] = target_id
                    clusters[target_id].update(clusters[cid])
                    del clusters[cid]
            for e in emails:
                email_to_cluster_id[e] = target_id
                clusters[target_id].add(e)
        else:
            cid = next_cluster_id
            next_cluster_id += 1
            clusters[cid] = set(emails)
            for e in emails:
                email_to_cluster_id[e] = cid

    # Then: assign remaining emails (external, no cross-domain match) each to their own cluster
    for email in email_names:
        if email not in email_to_cluster_id:
            cid = next_cluster_id
            next_cluster_id += 1
            clusters[cid] = {email}
            email_to_cluster_id[email] = cid

    log.info("Phase 2: %d emails → %d person clusters (%d cross-domain merges)",
             len(email_names), len(clusters),
             sum(1 for c in clusters.values() if len(c) > 1))

    # ── Phase 3: Build person records ────────────────────────────────
    persons = []
    email_to_person_id: dict[str, str] = {}

    for cid, emails in sorted(clusters.items()):
        # Merge name counts across all emails in cluster
        merged_names: Counter = Counter()
        for email in emails:
            merged_names.update(email_names[email])

        canonical_name = select_canonical_name(merged_names)

        # Determine primary email (highest activity)
        primary_email = max(emails, key=lambda e: email_sent.get(e, 0) + email_received.get(e, 0))

        # Determine if internal
        domains = {e.split("@")[-1] for e in emails if "@" in e}
        is_internal = bool(domains & org_domain_set)

        # Aggregate stats
        total_sent = sum(email_sent.get(e, 0) for e in emails)
        total_received = sum(email_received.get(e, 0) for e in emails)
        all_threads = set()
        all_contacts = set()
        for e in emails:
            all_threads.update(email_threads.get(e, set()))
            all_contacts.update(email_contacts.get(e, set()))
        # Remove self-contacts
        all_contacts -= emails

        # ── Sender-type classification (F3 fix) — headers + behavior, no LLM.
        # Signals aggregated across every email in the cluster.
        features = build_features(
            primary_email,
            is_org_domain=is_internal,
            is_bot=bool(emails & email_is_bot),
            had_list_unsubscribe=bool(emails & email_list_unsub),
            promo_label=bool(emails & email_promo),
            extra_functional=functional_emails,
            noise_domains=noise_domains,
            allowlisted_domains=allowlisted_domains,
            personal_domains=PERSONAL_DOMAINS,
            emails_sent=total_sent,
            emails_received=total_received,
            unique_contacts=len(all_contacts),
        )
        sender_type, sender_type_reason = classify_sender(features)
        is_functional = sender_type == SenderType.FUNCTIONAL_MAILBOX

        # Generate stable person ID from primary email
        person_id = f"person:{primary_email}"

        person = {
            "person_id": person_id,
            "canonical_name": canonical_name,
            "primary_email": primary_email,
            "all_emails": sorted(emails),
            "all_names": [n for n, _ in merged_names.most_common(10) if n and not _is_garbage_name(n)],
            "is_internal": is_internal,
            "is_functional": is_functional,     # back-compat alias of sender_type
            "is_human": sender_type == SenderType.HUMAN,
            "sender_type": sender_type.value,
            "sender_type_reason": sender_type_reason,
            "domains": sorted(domains),
            "stats": {
                "emails_sent": total_sent,
                "emails_received": total_received,
                "total_activity": total_sent + total_received,
                "threads_participated": len(all_threads),
                "unique_contacts": len(all_contacts),
            },
        }

        persons.append(person)
        for e in emails:
            email_to_person_id[e] = person_id

    # Sort persons by activity (most active first)
    persons.sort(key=lambda p: -p["stats"]["total_activity"])

    elapsed = time.time() - start

    # Summary stats
    internal_count = sum(1 for p in persons if p["is_internal"])
    external_count = sum(1 for p in persons if not p["is_internal"])
    functional_count = sum(1 for p in persons if p["is_functional"])
    multi_email = sum(1 for p in persons if len(p["all_emails"]) > 1)
    sender_type_dist: Counter = Counter(p["sender_type"] for p in persons)
    humans = sender_type_dist.get(SenderType.HUMAN.value, 0)

    stats = {
        "total_persons": len(persons),
        "internal": internal_count,
        "external": external_count,
        "functional": functional_count,
        "multi_email_persons": multi_email,
        "total_emails_scanned": len(email_names),
        "events_processed": event_count,
        "duration_seconds": round(elapsed, 1),
        "sender_types": dict(sender_type_dist),
    }

    log.info("Phase 3: Built %d person records (%.1fs)", len(persons), elapsed)
    log.info("  Internal: %d | External: %d | Functional: %d | Multi-email: %d",
             internal_count, external_count, functional_count, multi_email)
    log.info("  Sender types: %d human, %d non-human (%s)",
             humans, len(persons) - humans, dict(sender_type_dist))

    return {
        "persons": persons,
        "email_to_person_id": email_to_person_id,
        "stats": stats,
    }


# ══════════════════════════════════════════════════════════════════════
# PHASE 4: NAME-BASED IDENTITY RESOLUTION
# ══════════════════════════════════════════════════════════════════════

PERSONAL_DOMAINS = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com',
                    'rediffmail.com', 'protonmail.com', 'yahoo.co.in',
                    'live.com', 'icloud.com', 'aol.com'}

FUNCTIONAL_NAMES = {'hr', 'support', 'admin', 'accounts', 'sales', 'info',
                    'contact', 'team', 'ops', 'operations', 'finance',
                    'legal', 'billing', 'marketing', 'noreply', 'descript'}


def _is_generic_name(name: str) -> bool:
    """Names too ambiguous for name-based merge."""
    name = name.strip().lower()
    if not name:
        return True
    if len(name) < 4:
        return True
    if ' ' not in name:
        return True
    words = name.split()
    if len(words) == 1:
        return True
    if name in FUNCTIONAL_NAMES or words[0] in FUNCTIONAL_NAMES:
        return True
    return False


def merge_by_name(registry: dict, internal_domains: set = None) -> tuple[dict, list]:
    """
    Merge persons who share the same canonical name where at least one is internal.
    Also merges external-only groups where all emails are personal domains.

    Returns (updated_registry, merge_log).
    """
    if not internal_domains:
        log.warning(
            "merge_by_name called without internal_domains — name-based merges will not "
            "correctly identify internal persons. Pass org_domains from org config."
        )
    internal_doms = internal_domains or set()
    persons = registry['persons']
    email_to_pid = dict(registry.get('email_to_person_id', {}))

    # Group by canonical_name (case-insensitive). Non-human senders (system,
    # newsletter, functional, marketing) never enter the alias graph — this is
    # the direct F3 fix: `rupesh garg ×9`, `atlassian ×16` etc. never merge.
    # Records without sender_type (e.g. Jira-augmented) default to human.
    name_groups: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(persons):
        if p.get('sender_type', SenderType.HUMAN.value) != SenderType.HUMAN.value:
            continue
        cn = p.get('canonical_name', '').strip().lower()
        if cn and not _is_generic_name(cn):
            name_groups[cn].append(i)

    merge_log = []
    indices_to_remove = set()

    for _, indices in name_groups.items():
        if len(indices) < 2:
            continue

        group = [persons[i] for i in indices]

        # Classify group
        internal_indices = [i for i, p in zip(indices, group) if p.get('is_internal')]
        external_indices = [i for i, p in zip(indices, group) if not p.get('is_internal')]

        target_idx = None
        merge_from_indices = []
        reason = ''
        confidence = 0.0

        if internal_indices:
            # Rule (a): At least one internal — merge all into the most active internal
            target_idx = max(internal_indices,
                             key=lambda i: persons[i]['stats']['total_activity'])
            merge_from_indices = [i for i in indices if i != target_idx]
            reason = 'same_name_internal_match'
            confidence = 0.95 if external_indices else 0.90

        else:
            # All external — check domain overlap
            all_domains = set()
            for p in group:
                for e in p.get('all_emails', []):
                    if '@' in e:
                        all_domains.add(e.split('@')[1])

            personal_only = all_domains.issubset(PERSONAL_DOMAINS)
            non_personal = all_domains - PERSONAL_DOMAINS

            if personal_only:
                # Rule (c): All personal emails, same name — likely same person
                target_idx = max(indices,
                                 key=lambda i: persons[i]['stats']['total_activity'])
                merge_from_indices = [i for i in indices if i != target_idx]
                reason = 'same_name_personal_emails'
                confidence = 0.80

            elif len(non_personal) <= 1:
                # Rule (c): Same company domain + personal emails
                target_idx = max(indices,
                                 key=lambda i: persons[i]['stats']['total_activity'])
                merge_from_indices = [i for i in indices if i != target_idx]
                reason = 'same_name_same_company'
                confidence = 0.85

            else:
                # Rule (b): Multiple different companies — check thread co-occurrence
                # Multiple different companies, no thread data available here
                # Don't merge — likely different people
                continue

        if target_idx is None or not merge_from_indices:
            continue

        target = persons[target_idx]

        # Perform merge
        merged_emails = set(target.get('all_emails', []))
        merged_names_set = set(target.get('all_names', []))
        merged_domains = set(target.get('domains', []))
        merged_from_ids = []

        for idx in merge_from_indices:
            src = persons[idx]
            merged_from_ids.append(src['person_id'])
            merged_emails.update(src.get('all_emails', []))
            merged_names_set.update(src.get('all_names', []))
            merged_domains.update(src.get('domains', []))

            # Sum stats
            for stat_key in ('emails_sent', 'emails_received', 'total_activity',
                             'threads_participated', 'unique_contacts'):
                target['stats'][stat_key] = (
                    target['stats'].get(stat_key, 0) + src['stats'].get(stat_key, 0)
                )

            indices_to_remove.add(idx)

        target['all_emails'] = sorted(merged_emails)
        target['all_names'] = [n for n in merged_names_set if n and not _is_garbage_name(n)][:15]
        target['domains'] = sorted(merged_domains)
        target['merge_source'] = reason

        # Update is_internal if we absorbed an internal email
        if any(d in internal_doms for d in target['domains']):
            target['is_internal'] = True

        # Update email lookup
        for e in merged_emails:
            email_to_pid[e] = target['person_id']

        merge_log.append({
            'merged_into': target['person_id'],
            'merged_from': merged_from_ids,
            'reason': reason,
            'canonical_name': target['canonical_name'],
            'confidence': confidence,
            'emails_after': target['all_emails'],
        })

    # Remove absorbed persons (iterate in reverse to preserve indices)
    new_persons = [p for i, p in enumerate(persons) if i not in indices_to_remove]

    # Re-sort by activity
    new_persons.sort(key=lambda p: -p['stats']['total_activity'])

    new_registry = dict(registry)
    new_registry['persons'] = new_persons
    new_registry['email_to_person_id'] = email_to_pid

    # Update stats
    new_registry['stats'] = dict(registry.get('stats', {}))
    new_registry['stats']['total_persons'] = len(new_persons)
    new_registry['stats']['internal'] = sum(1 for p in new_persons if p.get('is_internal'))
    new_registry['stats']['external'] = sum(1 for p in new_persons if not p.get('is_internal'))
    new_registry['stats']['multi_email_persons'] = sum(
        1 for p in new_persons if len(p.get('all_emails', [])) > 1)
    new_registry['stats']['name_merges'] = len(merge_log)
    new_registry['stats']['persons_absorbed'] = len(indices_to_remove)

    return new_registry, merge_log


# ══════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════

_USERNAME_PATTERN = re.compile(r"^[a-z0-9_.\-]{3,}$")


def _looks_like_username(name: str) -> bool:
    """Return True if name looks like a machine handle rather than a real name."""
    if not name:
        return False
    # Has a space → likely a real name ("Parth Shah")
    if " " in name:
        return False
    return bool(_USERNAME_PATTERN.match(name))


def augment_registry_from_jira(registry: dict, org: str, data_dir: Path) -> int:
    """Enrich the registry with Jira principal data from the discovery artifact.

    Three things happen per principal:
    1. If the principal's email already exists in the registry (from Gmail),
       add their Jira display name to all_names so the validator can resolve
       LLM-extracted name variants (e.g. old display names) without minting
       a duplicate person:manual node.
    2. If the principal is new (email not in registry or no email), create a
       lightweight Person record keyed on their email or accountId.
    3. If jira_display_name_overrides is set in org config for this accountId,
       use the override name instead of the Jira display name.

    Returns the number of new persons added.
    """
    try:
        from common.output_paths import JiraPaths
        art_path = JiraPaths(org=org, data_dir=data_dir).scope_principals
        if not art_path.exists():
            return 0
        artifact = json.loads(art_path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    # Load display name overrides from org config
    config_dir = data_dir / "config"
    org_config = {}
    try:
        from common.org_config import load_org_config
        org_config = load_org_config(org, config_dir)
    except Exception:
        pass
    display_name_overrides: dict[str, str] = org_config.get("jira_display_name_overrides", {})

    existing_pids = {p.get("person_id") for p in registry.get("persons", [])}
    existing_emails = set(registry.get("email_to_person_id", {}).keys())
    # Build pid -> person record map for O(1) enrichment lookups
    pid_to_person = {p["person_id"]: p for p in registry.get("persons", [])}
    added = 0

    for principal in artifact.get("principals", []):
        if principal.get("account_type") == "app":
            continue  # skip bots

        pid_raw = principal.get("principal_id", "")
        email = (principal.get("email") or "").strip().lower()
        display_name = (principal.get("display_name") or pid_raw).strip()

        # Apply org config override if present (bare accountId, no jira:U prefix)
        bare_account_id = scopes.strip_jira_user(pid_raw)
        if bare_account_id in display_name_overrides:
            display_name = display_name_overrides[bare_account_id].strip()
        elif _looks_like_username(display_name):
            log.warning(
                "Jira principal %s has a username-like display name %r — "
                "add a jira_display_name_overrides entry in org config to set a real name",
                pid_raw, display_name,
            )

        # If already in registry by email, enrich all_names with Jira display name
        # so the validator can resolve LLM-extracted name variants (e.g. old display
        # names) back to this person without creating a duplicate person:manual node.
        if email and email in existing_emails:
            if display_name and not _is_garbage_name(display_name):
                existing_pid = registry["email_to_person_id"].get(email)
                person_rec = pid_to_person.get(existing_pid)
                if person_rec is not None:
                    names = person_rec.setdefault("all_names", [])
                    if display_name not in names:
                        names.append(display_name)
                    # Also update canonical_name if current one is a garbage handle
                    if _is_garbage_name(person_rec.get("canonical_name", "")):
                        person_rec["canonical_name"] = display_name
            continue

        # Create a new person record keyed on email (if available) or accountId
        person_id = f"person:{email}" if email else f"person:{pid_raw}"
        if person_id is None:
            continue
        if person_id in existing_pids:
            continue

        person = {
            "person_id": person_id,
            "canonical_name": display_name,
            "primary_email": "",
            "all_emails": [],
            "all_names": [display_name] if display_name else [],
            "is_internal": True,
            "is_functional": False,
            "domains": [],
            "principal_id": pid_raw,
            "source": "jira_discovery",
            "stats": {
                "emails_sent": 0, "emails_received": 0,
                "total_activity": 0, "threads_participated": 0,
                "unique_contacts": 0,
            },
        }
        registry.setdefault("persons", []).append(person)
        registry.setdefault("email_to_person_id", {})[pid_raw] = person_id
        # Also map the bare accountId (strip jira:U prefix) for lookup
        if scopes.is_jira_user(pid_raw):
            acct = scopes.strip_jira_user(pid_raw)
            registry["email_to_person_id"].setdefault(acct, person_id)

        existing_pids.add(person_id)
        added += 1

    return added


def save_registry(registry: dict, output_dir: Path, org: str):
    """Save the person registry to disk."""
    # Full registry
    registry_path = output_dir / f"{org}_person_registry.json"
    registry_path.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved person registry → %s", registry_path)

    # Compact email→person lookup (for fast loading in downstream tools)
    lookup_path = output_dir / f"{org}_email_lookup.json"
    lookup_path.write_text(
        json.dumps(registry["email_to_person_id"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("Saved email lookup → %s", lookup_path)

    # Sender-type map (G3): {email: sender_type} — the extractor loads this to
    # skip threads whose every participant is a skippable non-human sender.
    sender_types = {
        e: p.get("sender_type", "human")
        for p in registry["persons"]
        for e in p.get("all_emails", [])
    }
    st_path = output_dir / f"{org}_sender_types.json"
    st_path.write_text(json.dumps(sender_types, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    log.info("Saved sender-type map → %s", st_path)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build person registry from ingested events")
    parser.add_argument("--input", required=True, help="Path to filtered JSONL file")
    parser.add_argument("--org", required=True, help="Organization name")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--extra-domains", nargs="*", default=None,
                        help="Additional org domains beyond what's in config")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if the registry has 0 persons.")

    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent.parent  # parent of filtered/

    from common.output_paths import KnowledgePaths
    paths = KnowledgePaths(org=args.org, data_dir=output_dir)
    paths.makedirs()
    config_dir = output_dir / "config"
    logs_dir = output_dir / "logs"
    for d in (config_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    from common.logger import setup_logging
    setup_logging(org=args.org, log_dir=str(logs_dir), source="person_registry")

    # Load org config for domains
    org_config = load_org_config(args.org, config_dir)
    org_domains = org_config.get("org_domains", [])
    if args.extra_domains:
        org_domains = list(set(org_domains + args.extra_domains))

    functional = set(org_config.get("extra_functional_mailboxes", []))
    noise_domains = set(org_config.get("discovered_noise_domains", []))
    allowlisted = set(org_config.get("allowlisted_domains", []))

    if not org_domains:
        log.warning("No org domains configured — cross-domain alias detection will be limited")

    registry = build_person_registry(
        input_path=input_path,
        org_domains=org_domains,
        functional_emails=functional,
        noise_domains=noise_domains,
        allowlisted_domains=allowlisted,
    )

    # Phase 4: Name-based merge (personal Gmail aliases etc.)
    before_count = len(registry["persons"])
    registry, merge_log = merge_by_name(registry, internal_domains=set(org_domains))
    after_count = len(registry["persons"])
    log.info("Phase 4: Name-based merge — %d persons absorbed (%d → %d), %d merge groups",
             before_count - after_count, before_count, after_count, len(merge_log))

    jira_added = augment_registry_from_jira(registry, args.org, output_dir)
    if jira_added:
        log.info("Augmented registry with %d Jira principals (no email)", jira_added)
        # Update stats to reflect Jira-augmented persons
        registry["stats"]["total_persons"] = len(registry["persons"])
        registry["stats"]["internal"] += jira_added

    save_registry(registry, paths.registry_dir, args.org)

    # Print summary
    stats = registry["stats"]
    persons = registry["persons"]

    log.info("")
    log.info("=" * 55)
    log.info("PERSON REGISTRY COMPLETE — %s", args.org)
    log.info("  Total persons     : %d", stats["total_persons"])
    log.info("  Internal (org)    : %d", stats["internal"])
    log.info("  External          : %d", stats["external"])
    log.info("  Functional        : %d", stats["functional"])
    log.info("  Multi-email       : %d", stats["multi_email_persons"])
    log.info("  Events processed  : %d", stats["events_processed"])
    log.info("  Duration          : %.1fs", stats["duration_seconds"])
    log.info("")
    log.info("  Top 15 by activity:")
    for p in persons[:15]:
        tag = "[INT]" if p["is_internal"] else "[EXT]"
        func = " [FUNC]" if p["is_functional"] else ""
        emails = ", ".join(p["all_emails"][:3])
        s = p["stats"]
        log.info("    %s%s %-25s sent=%-5d recv=%-5d contacts=%-4d  %s",
                 tag, func, p["canonical_name"][:25],
                 s["emails_sent"], s["emails_received"],
                 s["unique_contacts"], emails)
    log.info("=" * 55)

    log_summary(
        "person_registry",
        org=args.org,
        persons=stats["total_persons"],
        internal=stats["internal"],
        external=stats["external"],
        functional=stats["functional"],
        multi_email=stats["multi_email_persons"],
        events=stats["events_processed"],
        merged_by_name=before_count - after_count,
        duration_s=stats["duration_seconds"],
    )
    check_nonempty("person_registry", count=stats["total_persons"],
                   what="persons", allow_empty=args.allow_empty)


if __name__ == "__main__":
    main()
