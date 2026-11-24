"""
pipeline/tests/test_identity_resolver.py
─────────────────────────────────────────
Golden-fixture correctness test for the centralized resolver.

Merge correctness CANNOT be tested on the real data we have (EZO is Gmail-only; techtest's
ACL artifacts have no principals), because there is no ground-truth overlap. So we hand-label
a tiny fixture grounded in the exact shapes the real data shows — Jira hidden-email, a
personal-gmail Slack human, the generator bot, a functional mailbox, a cross-domain alias,
and a same-name-different-person collision — and assert exact resolver behaviour.

Run:    ../.venv/bin/python -m pipeline.tests.test_identity_resolver
or:     ../.venv/bin/pytest pipeline/tests/test_identity_resolver.py
"""

from __future__ import annotations

from pipeline.identity.source_identity import (
    SourceIdentity, INTERNAL, EXTERNAL, FUNCTIONAL, AUTOMATION,
)
from pipeline.identity.resolver import IdentityStore


def _si(source, native, emails, names, kind, **kw):
    return SourceIdentity(source=source, native_key=native, emails=emails,
                          names=names, kind=kind, **kw)


# ── the fixture: each comment is the ground-truth expectation ────────────────
def fixture() -> list[SourceIdentity]:
    return [
        # 1. RAVI — one human across all 3 sources. gmail+slack join by email;
        #    jira has the email HIDDEN and must Tier-2a name-match into the roster.
        _si("gmail", "email:ravi@ezobooks.in", ["ravi@ezobooks.in"], ["Ravi Kumar"], INTERNAL),
        _si("slack", "slack:U_ravi", ["ravi@ezobooks.in"], ["Ravi Kumar"], INTERNAL),
        _si("jira",  "jira:U_ravi", [], ["Ravi Kumar"], INTERNAL),               # email-less → Tier 2a

        # 2. MEERA — cross-domain alias on the gmail side; slack joins by the 2nd email.
        _si("gmail", "email:meera@ezobooks.in",
            ["meera@ezobooks.in", "meera@ezobanks.com"], ["Meera Patel"], INTERNAL),
        _si("slack", "slack:U_meera", ["meera@ezobanks.com"], ["Meera Patel"], INTERNAL),

        # 3. VENDOR — external, shared email links gmail+jira (deterministic, allowed).
        _si("gmail", "email:vendor@acme.com", ["vendor@acme.com"], ["Vendor Acme"], EXTERNAL),
        _si("jira",  "jira:U_vendor", ["vendor@acme.com"], ["Vendor Acme"], EXTERNAL),

        # 4. AMIT ×2 — SAME NAME, DIFFERENT PEOPLE. Distinct visible emails ⇒ must NOT merge.
        _si("gmail", "email:amit.shah@ezobooks.in", ["amit.shah@ezobooks.in"], ["Amit Shah"], INTERNAL),
        _si("jira",  "jira:U_amit2", ["amitshah2@ezobooks.in"], ["Amit Shah"], INTERNAL),

        # 5. BOT — automation, never merges into anyone.
        _si("slack", "slack:U_bot", [], ["Release Bot"], AUTOMATION),

        # 6. ACCOUNTS — functional mailbox; gmail+slack are the SAME mailbox (join by email),
        #    but it must never fuse with a human.
        _si("gmail", "email:accounts@ezobooks.in", ["accounts@ezobooks.in"], ["Accounts EZO"], FUNCTIONAL),
        _si("slack", "slack:U_acct", ["accounts@ezobooks.in"], ["Accounts"], FUNCTIONAL),

        # 7. JOHN — email-less EXTERNAL whose name resembles VENDOR. Externals are NEVER
        #    auto-merged on name ⇒ stays separate + emits a Tier-2b review suggestion.
        _si("jira",  "jira:U_john", [], ["Vendor Acme"], EXTERNAL),

        # 8. NINA — the registry MISSED a cross-domain merge (two separate gmail persons),
        #    but Slack carries BOTH addresses and bridges them ⇒ exercises merge()
        #    (two existing persons fused, one tombstoned, alias keys repointed).
        _si("gmail", "email:nina@ezobooks.in", ["nina@ezobooks.in"], ["Nina Rao"], INTERNAL),
        _si("gmail", "email:nina@ezobanks.com", ["nina@ezobanks.com"], ["Nina Rao"], INTERNAL),
        _si("slack", "slack:U_nina", ["nina@ezobooks.in", "nina@ezobanks.com"], ["Nina Rao"], INTERNAL),
    ]


def resolve_all(identities) -> IdentityStore:
    store = IdentityStore(org="fixture")
    # directory-first / emails-first: seed the roster before email-less actors name-match.
    for si in sorted(identities, key=lambda s: (0 if s.emails else 1)):
        store.resolve(si)
    return store


def pid_of(store, native_key) -> str | None:
    p = store.alias.get(native_key)
    return store._redirect(p) if p else None


# ── assertions ───────────────────────────────────────────────────────────────
def _run_golden() -> IdentityStore:
    store = resolve_all(fixture())
    live = [p for p in store.persons.values() if not p.get("merged_into")]

    P = lambda k: pid_of(store, k)
    ravi_g, ravi_s, ravi_j = P("email:ravi@ezobooks.in"), P("slack:U_ravi"), P("jira:U_ravi")
    meera_g, meera_s = P("email:meera@ezobooks.in"), P("slack:U_meera")
    vendor_g, vendor_j = P("email:vendor@acme.com"), P("jira:U_vendor")
    amit_g, amit_j = P("email:amit.shah@ezobooks.in"), P("jira:U_amit2")
    bot = P("slack:U_bot")
    acct_g, acct_s = P("email:accounts@ezobooks.in"), P("slack:U_acct")
    john = P("jira:U_john")
    nina_a, nina_b, nina_s = (P("email:nina@ezobooks.in"),
                              P("email:nina@ezobanks.com"), P("slack:U_nina"))

    # 1. Ravi: one person across all three sources (Tier-1b + Tier-2a)
    assert ravi_g == ravi_s == ravi_j, "Ravi should unify across gmail+slack+jira"
    assert sum(1 for v in store.persons[ravi_g]["sources"].values() if v) == 3

    # 2. Meera: cross-domain alias + slack join
    assert meera_g == meera_s, "Meera should join across the aliased domain"

    # 3. Vendor: external email join allowed
    assert vendor_g == vendor_j, "shared external email should join"
    assert store.persons[vendor_g]["kind"] == EXTERNAL

    # 4. Amit ×2: same name, different visible emails ⇒ separate
    assert amit_g != amit_j, "same-name different-email people must NOT merge"

    # 5. Bot: automation, isolated
    assert store.persons[bot]["kind"] == AUTOMATION
    assert sum(1 for v in store.persons[bot]["sources"].values() if v) == 1

    # 6. Accounts: functional, gmail+slack same mailbox, never a human
    assert acct_g == acct_s, "same functional mailbox should join across sources"
    assert store.persons[acct_g]["kind"] == FUNCTIONAL

    # 7. John: email-less external, NOT auto-merged into Vendor; emits a Tier-2b suggestion
    assert john != vendor_g, "externals must never auto-merge on name alone"
    assert any(r["type"] == "tier2b_suggestion" for r in store.review), "expected a Tier-2b suggestion"

    # 8. Nina: Slack bridged two missed-merge gmail persons ⇒ one person, a merge event fired
    assert nina_a == nina_b == nina_s, "Slack should bridge the two missed gmail persons"
    assert store.to_artifact()["counts"]["merges"] >= 1, "expected a merge() event (2 persons fused)"

    # exact population
    assert len(live) == 9, f"expected 9 distinct persons, got {len(live)}"

    # never merge across the human boundary
    for p in live:
        assert p["kind"] in (INTERNAL, EXTERNAL, FUNCTIONAL, AUTOMATION)

    return store


def test_golden_fixture():
    _run_golden()


def test_rerun_stable():
    """Seeding from a prior artifact keeps person_ids identical and mints no duplicates."""
    first = resolve_all(fixture())
    art1 = first.to_artifact()

    second = IdentityStore.from_artifact(art1)
    for si in sorted(fixture(), key=lambda s: (0 if s.emails else 1)):
        second.resolve(si)
    art2 = second.to_artifact()

    assert art1["counts"]["persons"] == art2["counts"]["persons"], "re-run changed person count"
    assert art1["alias_table"] == art2["alias_table"], "re-run changed alias→person_id mapping"


if __name__ == "__main__":
    store = _run_golden()
    test_rerun_stable()
    art = store.to_artifact()
    print("✅ golden fixture PASSED")
    print(f"   persons: {art['counts']['persons']}  by_kind: {art['counts']['by_kind']}")
    print(f"   merges:  {art['counts']['merges']}   alias keys: {art['counts']['alias_keys']}")
    print(f"   review:  {len(store.review)} item(s): {[r['type'] for r in store.review]}")
    print("✅ re-run stability PASSED")
