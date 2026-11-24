"""L1 registries (build step 2): the pure scan → resolve core.

Pins the four properties the registry layer exists for:

  1. org-domain aliases with one local part are ONE person (gk@ezobooks.in =
     gk@ezobanks.com) — anything else stands alone, no fuzzy merging here;
  2. sender-typing keeps non-humans out: a noreply sender is SYSTEM and mints
     no counterparty organization;
  3. organizations come only from human/functional traffic, kinded
     internal / external / personal_provider;
  4. conversation artifacts carry correct counts even when noise messages are
     part of the conversation (counted in message_count, not signal_count).
"""
from datetime import datetime, timezone

from builders.l1_registry import Scan, resolve

DOMAINS = ["ezobooks.in", "ezobanks.com"]


def _ev(sender, recipients, when="2026-01-05T10:00:00+00:00", conv="c1",
        signal=True, title="Quarterly numbers", names=None, bot=False, raw=None):
    names = names or {}
    actors = [{"email": e, "display_name": names.get(e, ""), "is_bot": bot and e == sender}
              for e in [sender, *recipients]]
    return {
        "actors": actors, "primary_actor": sender,
        "occurred_at": datetime.fromisoformat(when).astimezone(timezone.utc),
        "conversation_id": conv, "noise_class": "signal" if signal else "noise_domain_universal",
        "title": title, "raw": raw or {},
    }


def _run(events, **kw):
    scan = Scan()
    for e in events:
        scan.feed(e)
    return resolve(scan, "testorg", DOMAINS, **kw)


def _person(rows, email):
    return next(p for p in rows["persons"] if email in p["primary_email"])


def test_org_domain_aliases_merge_into_one_person():
    rows = _run([
        _ev("gk@ezobooks.in", ["ravi@ezobooks.in"], names={"gk@ezobooks.in": "Gaurav Kumar"}),
        _ev("gk@ezobanks.com", ["ravi@ezobooks.in"], conv="c2",
            names={"gk@ezobanks.com": "Gaurav Kumar"}),
    ])
    gk = _person(rows, "gk@")
    assert gk["alias_count"] == 2, "same local part across org domains = one person"
    assert gk["is_internal"] and gk["emails_sent"] == 2
    assert gk["display_name"] == "Gaurav Kumar"
    # both addresses point at the same person
    pids = {a["person_id"] for a in rows["aliases"] if a["email"].startswith("gk@")}
    assert pids == {gk["person_id"]}
    # but distinct local parts never merge
    assert _person(rows, "ravi@")["person_id"] != gk["person_id"]


def test_noreply_sender_is_system_and_mints_no_org():
    rows = _run([
        _ev("ravi@ezobooks.in", ["asha@client.com"], names={"asha@client.com": "Asha P"}),
        _ev("noreply@vendor.io", ["ravi@ezobooks.in"], conv="c2"),
    ])
    assert _person(rows, "noreply@")["sender_type"] == "system"
    assert "org:vendor.io" not in {o["org_id"] for o in rows["orgs"]}, \
        "system senders must not create counterparty organizations"


def test_org_kinds_internal_external_personal():
    rows = _run([
        _ev("ravi@ezobooks.in", ["asha@client.com", "friend@gmail.com"],
            names={"asha@client.com": "Asha P", "friend@gmail.com": "Friend"}),
    ])
    kinds = {o["domain"]: o["kind"] for o in rows["orgs"]}
    assert kinds["ezobooks.in"] == "internal"
    assert kinds["client.com"] == "external"
    assert kinds["gmail.com"] == "personal_provider"


def test_functional_mailbox_is_typed_but_kept():
    rows = _run([_ev("hr@ezobooks.in", ["ravi@ezobooks.in"],
                     names={"hr@ezobooks.in": "EZO HR"})])
    hr = _person(rows, "hr@")
    assert hr["sender_type"] == "functional_mailbox"
    assert hr["is_internal"], "hr@ carries real org knowledge — never dropped"


def test_conversation_artifact_counts_signal_and_noise_apart():
    rows = _run([
        _ev("ravi@ezobooks.in", ["asha@client.com"], when="2026-01-05T10:00:00+00:00",
            title="Kickoff"),
        _ev("asha@client.com", ["ravi@ezobooks.in"], when="2026-01-06T10:00:00+00:00",
            title="Re: Kickoff"),
        _ev("promo@ads.example", ["ravi@ezobooks.in"], when="2026-01-07T10:00:00+00:00",
            signal=False, title="SALE"),
    ])
    art = next(a for a in rows["artifacts"] if a["artifact_id"] == "c1")
    assert art["message_count"] == 3 and art["signal_count"] == 2
    assert art["participant_count"] == 2, "noise participants don't count"
    assert art["title"] == "Kickoff", "titled by its earliest message"
    assert art["first_at"] < art["last_at"]


def test_noise_only_sender_never_becomes_a_person():
    rows = _run([
        _ev("ravi@ezobooks.in", ["asha@client.com"]),
        _ev("blast@spam.example", ["ravi@ezobooks.in"], conv="c9", signal=False),
    ])
    assert not any("blast@" in p["primary_email"] for p in rows["persons"]), \
        "persons are built from signal events only"
