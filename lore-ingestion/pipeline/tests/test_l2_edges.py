"""L2 interaction graph (build step 3): the pure scan → resolve core.

Pins the properties that make the edges trustworthy and deterministic:

  1. AUTHORED only fires for the message sender, weighted by messages sent
     in that artifact;
  2. PARTICIPATED_IN fires for every resolved actor on a signal message,
     including the author — recipients who never replied still show up;
  3. COMMUNICATES_WITH is directed sender -> recipient and self-edges never
     appear;
  4. noise events are excluded entirely — no edges, no weight contribution.
"""
from datetime import datetime, timezone

from builders.l2_edges import Scan, resolve

E2P = {"gk@ezobooks.in": "person:gk@ezobooks.in",
       "ravi@ezobooks.in": "person:ravi@ezobooks.in",
       "asha@client.com": "person:asha@client.com"}


def _ev(sender, recipients, when="2026-01-05T10:00:00+00:00", conv="c1", signal=True):
    return {"actors": [{"email": e} for e in [sender, *recipients]],
            "primary_actor": sender,
            "occurred_at": datetime.fromisoformat(when).astimezone(timezone.utc),
            "conversation_id": conv, "noise_class": "signal" if signal else "noise_domain_universal"}


def _run(events):
    scan = Scan()
    for e in events:
        scan.feed(e, E2P)
    return resolve(scan, "testorg")


def test_authored_is_sender_only_weighted_by_messages():
    rows = _run([
        _ev("gk@ezobooks.in", ["ravi@ezobooks.in"]),
        _ev("gk@ezobooks.in", ["ravi@ezobooks.in"], when="2026-01-06T10:00:00+00:00"),
    ])
    a = next(r for r in rows["authored"] if r["person_id"] == "person:gk@ezobooks.in")
    assert a["weight"] == 2 and a["artifact_id"] == "c1"
    assert a["first_at"] < a["last_at"]
    assert not any(r["person_id"] == "person:ravi@ezobooks.in" for r in rows["authored"]), \
        "a recipient who never sent must not get an AUTHORED edge"


def test_participated_in_includes_the_author():
    rows = _run([_ev("gk@ezobooks.in", ["ravi@ezobooks.in", "asha@client.com"])])
    pids = {r["person_id"] for r in rows["participated_in"] if r["artifact_id"] == "c1"}
    assert pids == {"person:gk@ezobooks.in", "person:ravi@ezobooks.in", "person:asha@client.com"}


def test_communicates_with_is_directed_and_no_self_edges():
    rows = _run([_ev("gk@ezobooks.in", ["ravi@ezobooks.in"])])
    edges = {(r["person_id"], r["other_person_id"]) for r in rows["communicates_with"]}
    assert edges == {("person:gk@ezobooks.in", "person:ravi@ezobooks.in")}


def test_noise_events_produce_no_edges():
    rows = _run([_ev("gk@ezobooks.in", ["ravi@ezobooks.in"], signal=False)])
    assert rows["authored"] == [] and rows["participated_in"] == [] and rows["communicates_with"] == []
