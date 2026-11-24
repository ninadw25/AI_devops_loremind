"""The shared L0 sync core (runners/l0_sync.py): the durability contract every
source now inherits instead of re-implementing.

Pins the properties that made the per-source clones correct, now proven once:

  1. per-batch writes (durable mid-fetch), not one write at the end;
  2. the cursor advances LAST and only when it moved (crash-safe resume);
  3. a resume passes the stored cursor to the fetch closure;
  4. dry-run fetches but never writes and never advances a cursor;
  5. one principal's failure is isolated — its cursor stays put, the rest run;
  6. the sightings + payload id are built from the source's own L0 module.
"""
import runners.l0_sync as core


class FakeL0:
    L0_PAYLOAD_KIND = "test.payload"
    @staticmethod
    def canonical_id(m): return f"cid:{m['id']}"
    @staticmethod
    def canonical_payload(m): return {"body": m["id"]}
    @staticmethod
    def sighting(m, principal): return {"source_local_id": m["id"], "who": principal}
    @staticmethod
    def internal_date_ms(m): return m["ms"]


class FakeStore:
    def __init__(self, cursors=None):
        self.cursors = dict(cursors or {})
        self.writes = []            # each upsert_payloads call's rows
        self.cursor_sets = []       # (principal, ms)

    def get_fetch_cursor(self, org, source, principal):
        return self.cursors.get(principal)

    def upsert_payloads(self, rows):
        self.writes.append(rows)
        return {"inserted": len(rows), "merged": 0}

    def set_fetch_cursor(self, org, source, principal, last_internal_date_ms):
        self.cursor_sets.append((principal, last_internal_date_ms))

    def count_payloads(self, org, source):
        return sum(len(w) for w in self.writes)


def _msgs(*specs):
    return [{"id": i, "ms": ms} for i, ms in specs]


def test_writes_per_batch_and_advances_cursor_last():
    store = FakeStore()
    # fetch closure emits two separate batches for the one principal
    def fetch(principal, since_ms, emit):
        emit(_msgs(("a", 100), ("b", 200)))
        emit(_msgs(("c", 300)))
    totals = core.run_l0_sync("org", "test", [{"p": "acct1"}], fetch, FakeL0,
                              store=store, label=lambda p: p["p"], parallel=1)
    assert len(store.writes) == 2, "each batch is its own durable write, not one at the end"
    assert totals["inserted"] == 3 and totals["done"] == 1
    # cursor advanced once, to the max ms seen
    assert store.cursor_sets == [("acct1", 300)]
    # payload id + sightings came from the L0 module
    assert store.writes[0][0]["sightings"] == {"acct1": {"source_local_id": "a", "who": "acct1"}}
    assert store.writes[0][0]["canonical_id"] == "cid:a"


def test_resume_passes_stored_cursor_and_skips_advance_when_nothing_new():
    store = FakeStore(cursors={"acct1": 300})
    seen = {}
    def fetch(principal, since_ms, emit):
        seen["since"] = since_ms          # closure receives the stored cursor
        # nothing newer than the cursor → emit nothing
    core.run_l0_sync("org", "test", [{"p": "acct1"}], fetch, FakeL0,
                     store=store, label=lambda p: p["p"], parallel=1)
    assert seen["since"] == 300, "resume hands the stored cursor to the fetch closure"
    assert store.cursor_sets == [], "no new mail → cursor must not be re-advanced"


def test_dry_run_fetches_but_never_writes_or_advances():
    store = FakeStore()
    def fetch(principal, since_ms, emit):
        emit(_msgs(("a", 100)))
    totals = core.run_l0_sync("org", "test", [{"p": "acct1"}], fetch, FakeL0,
                              store=None, label=lambda p: p["p"], dry_run=True, parallel=1)
    assert store.writes == [] and store.cursor_sets == []
    assert totals["fetched"] == 1 and totals["done"] == 0, "dry-run counts fetch, not completion"


def test_one_principal_failure_is_isolated():
    store = FakeStore()
    def fetch(principal, since_ms, emit):
        if principal == "bad":
            raise RuntimeError("boom")
        emit(_msgs(("a", 100)))
    totals = core.run_l0_sync(
        "org", "test", [{"p": "good"}, {"p": "bad"}], fetch, FakeL0,
        store=store, label=lambda p: p["p"], parallel=1)
    assert totals["failed"] == 1 and totals["done"] == 1
    assert "bad" in totals["failed_principals"]
    # the good principal still wrote + advanced; the bad one advanced nothing
    assert ("good", 100) in store.cursor_sets
    assert all(p != "bad" for p, _ in store.cursor_sets), "failed principal's cursor stays put"
