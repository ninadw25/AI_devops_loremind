"""
pipeline/validation/name_lookup.py
──────────────────────────────────
Person-name lookup + fuzzy resolution via rapidfuzz.

Used by the validator's triple-level checks to map LLM-emitted Person
names back to the canonical registry. Handles casing, parenthetical
annotations, partial names when unambiguous, and minor typos.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process


def build_name_lookup(registry: dict) -> dict[str, str]:
    """Build case-insensitive name → person_id lookup from registry.

    Returns dict mapping lowercase name → person_id for canonical_name
    and all all_names variants.
    """
    lookup: dict[str, str] = {}
    for person in registry.get("persons", []):
        pid = person["person_id"]
        canonical = person.get("canonical_name", "")
        if canonical:
            lookup[canonical.lower()] = pid
        for name in person.get("all_names", []):
            if name and len(name) > 1:
                lookup[name.lower()] = pid
    return lookup


# Minimum token_set_ratio score (0-100) to accept a fuzzy match.
# token_set_ratio compares token sets — gives 100 for partial-name
# matches (e.g., "Mukesh" → "Mukesh Sethia"), ~92-96 for typos, and
# low scores for spurious substring matches (e.g., "hi" in "Qureshi").
FUZZY_SCORE_CUTOFF = 85

# Minimum score gap between best and second-best match to consider
# the best match unambiguous. Prevents "Mukesh" from resolving when
# both "Mukesh Sethia" and "Mukesh Baral" exist (both would score 100).
FUZZY_AMBIGUITY_GAP = 5


def resolve_person_name(
    query: str,
    name_lookup: dict,
    persons_by_id: dict | None = None,
) -> tuple[str | None, str | None]:
    """Resolve a person name to a registry entry via fuzzy matching.

    Uses rapidfuzz.fuzz.token_set_ratio — compares sets of tokens,
    robust to word reordering and extra tokens. Handles casing,
    parenthetical annotations ("Tanvi Chaughule (10220)"), partial
    names when unambiguous, and typos within tokens.

    Returns (canonical_name, person_id) if resolved, (None, None) otherwise.
    Rejects email-like strings and very short queries. Returns None
    when multiple registry entries match with similar scores.
    """
    if not query or '@' in query:
        return None, None

    q = query.lower().strip()
    if len(q) < 3:
        return None, None

    # Fast path: exact match wins unconditionally (no ambiguity check).
    # Without this, "Mukesh Sethia" would fail because "mukesh" also
    # scores 100 via token_set_ratio, triggering ambiguity rejection.
    if q in name_lookup:
        pid = name_lookup[q]
        canonical = ((persons_by_id or {}).get(pid, {})
                     .get("canonical_name")) or query
        return canonical, pid

    # Fuzzy path: top 2 candidates, ambiguity check
    results = process.extract(
        q,
        name_lookup.keys(),
        scorer=fuzz.token_set_ratio,
        limit=2,
        score_cutoff=FUZZY_SCORE_CUTOFF,
    )

    if not results:
        return None, None

    best_match, best_score, _ = results[0]

    # Ambiguity check: if second match is within gap, bail out
    if len(results) > 1:
        _, second_score, _ = results[1]
        if best_score - second_score < FUZZY_AMBIGUITY_GAP:
            return None, None

    pid = name_lookup[best_match]
    canonical = ((persons_by_id or {}).get(pid, {})
                 .get("canonical_name")) or query
    return canonical, pid


def resolve_person_names_in_triple(
    triple: dict,
    name_lookup: dict,
    persons_by_id: dict,
) -> dict:
    """Try to fuzzy-resolve Person names in a triple, normalizing to canonical.

    Modifies the triple in place. For Person nodes whose name doesn't
    exactly match the registry, tries fuzzy resolution. If successful,
    replaces the name with the canonical version so the graph stays
    consistent. Adds _fuzzy_resolved flag when resolution was needed.
    """
    for node_key in ("subject", "object"):
        node = triple.get(node_key, {})
        if node.get("type") != "Person":
            continue
        name = node.get("name") or node.get("description") or ""
        if not name:
            continue

        name_lower = name.lower()
        # Already an exact match — normalize casing only
        if name_lower in name_lookup:
            pid = name_lookup[name_lower]
            canonical = persons_by_id.get(pid, {}).get("canonical_name")
            if canonical and canonical != name:
                if "name" in node:
                    node["name"] = canonical
                else:
                    node["description"] = canonical
            continue

        # Quick path: if the LLM emitted an email-like identifier, try
        # to resolve it via the registry's email lists (primary/all_emails).
        # This handles cases where the model returns addresses like
        # 'user@example.com' instead of a human-readable name.
        if "@" in name:
            for p in (persons_by_id or {}).values():
                emails = p.get("all_emails", []) or []
                primary = p.get("primary_email")
                if name == primary or name in emails:
                    canonical = p.get("canonical_name")
                    if canonical:
                        if "name" in node:
                            node["name"] = canonical
                        else:
                            node["description"] = canonical
                        node["_email_resolved"] = True
                        break
            # If we resolved by email, skip fuzzy matching
            if node.get("_email_resolved"):
                continue

        # Try fuzzy resolution
        canonical, pid = resolve_person_name(
            name, name_lookup, persons_by_id)
        if canonical and pid:
            if "name" in node:
                node["name"] = canonical
            else:
                node["description"] = canonical
            node["_fuzzy_resolved"] = True

    return triple
