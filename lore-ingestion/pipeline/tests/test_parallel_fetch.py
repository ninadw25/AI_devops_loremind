"""Parallel fetch: quota pacing + pipelined batches + parallel mailboxes.

docs/PARALLEL_FETCH.md. Three properties keep "as fast as the rate limit
allows" from becoming "a 429 storm" or "lost messages":

  1. the token bucket paces spend and adapts — 429 halves it, clean batches
     recover it;
  2. pipelined batches within a mailbox lose nothing and duplicate nothing;
  3. parallel mailboxes in the runner aggregate their totals correctly.

All against fakes — no network, no DB.
"""
import pytest

from infra.gmail.fetcher import GmailFetcher
from infra.gmail.throttle import BUCKET_CAPACITY, QuotaThrottle


# ── 1. The throttle ─────────────────────────────────────────────────────


class _Clock:
    """Manual clock: sleep() advances time — no real waiting in tests."""

    def __init__(self):
        self.now = 0.0
        self.slept = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.slept += s


def _throttle(rate=100.0, **kw):
    c = _Clock()
    return QuotaThrottle(rate=rate, clock=c, sleep=c.sleep, **kw), c


def test_full_bucket_spends_without_waiting():
    t, c = _throttle()
    t.acquire(BUCKET_CAPACITY)          # whole bucket available up front
    assert c.slept == 0, "a full bucket must not block"


def test_empty_bucket_blocks_until_refill():
    t, c = _throttle(rate=100.0)
    t.acquire(BUCKET_CAPACITY)          # drain it
    t.acquire(100)                      # needs 100 units at 100/s → ~1s wait
    assert c.slept == pytest.approx(1.0, abs=0.02)


def test_429_halves_the_pace_and_clean_batches_recover_it():
    t, _ = _throttle(rate=200.0, floor=25.0, step=5.0)
    t.penalize()
    assert t.rate == 100.0
    for _ in range(30):                 # halving continues but never below floor
        t.penalize()
    assert t.rate == 25.0
    for _ in range(1000):               # recovery is additive and capped
        t.reward()
    assert t.rate == 200.0, "recovery must stop at the target, not exceed it"


def test_pace_actually_slows_after_penalty():
    t, c = _throttle(rate=200.0)
    t.acquire(BUCKET_CAPACITY)          # drain
    t.acquire(100)                      # 100 units at 200/s → 0.5s
    before = c.slept
    t.penalize()                        # rate now 100/s
    t.acquire(100)                      # same spend → ~1s
    assert (c.slept - before) == pytest.approx(1.0, abs=0.05)


# ── 2. Pipelined batches within one mailbox ─────────────────────────────


class _FakeGmail:
    def __init__(self, ids):
        self._ids = ids

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        return self

    def execute(self):
        return {"messages": [{"id": i, "threadId": "t"} for i in self._ids]}


def _fetcher(monkeypatch, ids, batch_size=2, depth=3):
    f = GmailFetcher.__new__(GmailFetcher)
    f.domain, f.batch_size, f.batch_pause = "x.com", batch_size, 0
    f.units_per_sec, f.pipeline_depth = 1e9, depth
    monkeypatch.setattr(f, "_build_credentials", lambda email: None)
    monkeypatch.setattr("infra.gmail.fetcher.build", lambda *a, **k: _FakeGmail(ids))
    monkeypatch.setattr(f, "_fetch_batch",
                        lambda svc, chunk, prefix="", throttle=None:
                            [{"id": c["id"]} for c in chunk])
    return f


def test_pipelined_fetch_loses_nothing_and_duplicates_nothing(monkeypatch):
    ids = [f"m{i}" for i in range(97)]  # odd count → ragged last batch
    f = _fetcher(monkeypatch, ids, batch_size=5, depth=4)

    seen = []
    f.fetch_user_messages(email="a@x.com", on_batch=seen.append)

    got = [m["id"] for b in seen for m in b]
    assert sorted(got) == sorted(ids), "every message exactly once, any order"


def test_one_failed_batch_fails_the_mailbox(monkeypatch):
    """The runner's contract: a mailbox either completes or reports failure so
    its cursor stays put. A batch error must surface, not vanish into the pool."""
    ids = [f"m{i}" for i in range(20)]
    f = _fetcher(monkeypatch, ids, batch_size=5, depth=2)

    def _boom(svc, chunk, prefix="", throttle=None):
        if any(c["id"] == "m7" for c in chunk):
            raise TimeoutError("batch died")
        return [{"id": c["id"]} for c in chunk]

    monkeypatch.setattr(f, "_fetch_batch", _boom)
    with pytest.raises(TimeoutError):
        f.fetch_user_messages(email="a@x.com", on_batch=lambda b: None)


# ── 3. Parallel mailboxes in the runner ─────────────────────────────────


def test_parallel_runner_aggregates_all_mailboxes(monkeypatch, tmp_path):
    """4 workers over 6 mailboxes (one failing): totals must add up exactly —
    shared-dict races here would silently under- or double-count."""
    import runners.ledger_sync as ls

    per_box = {f"u{i}@x.com": [{"id": f"u{i}-m{j}", "internalDate": "5"}
                               for j in range(10)] for i in range(6)}

    class _FakeFetcher:
        def __init__(self, **kw):
            pass

        def fetch_user_messages(self, email, on_batch=None, **kw):
            if email == "u3@x.com":
                raise TimeoutError("mailbox down")
            for k in range(0, 10, 4):           # ragged batches of 4/4/2
                on_batch(per_box[email][k:k + 4])
            return []

    monkeypatch.setattr(ls, "GmailFetcher", _FakeFetcher)

    users = [{"email": e} for e in per_box]
    totals = ls.sync(org="t", domain="x.com", admin_email="a@x.com", key="k.json",
                     users=users, dry_run=True, output_dir=str(tmp_path), parallel=4)

    assert totals["fetched"] == 50, "5 good mailboxes x 10 messages"
    # Return contract is now source-agnostic (shared runners/l0_sync core):
    # 'failed_users' → 'failed_principals', 'users' → 'principals'.
    assert totals["failed"] == 1 and totals["failed_principals"] == ["u3@x.com"]
