"""A tenant fetched entirely BEFORE the daemon ever started must still be
processed on its first cycle — 'data presence IS the work queue' (module
docstring), not just deltas that occur after launch.

Regression: main() used to seed last_payloads/last_events/last_claims from
the SAME counts() call it logged at startup, so a pre-existing backlog
(131205 payloads, 0 events) compared 131205 against a baseline of 131205 and
concluded nothing had changed — a fetched-but-never-processed tenant would
sit idle forever, 300 seconds at a time.
"""
from __future__ import annotations

import sys

from runners import substrate_flow


def test_first_cycle_processes_a_preexisting_backlog(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["substrate_flow", "--org", "acme", "--once"])
    monkeypatch.setattr(substrate_flow, "load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setattr(substrate_flow, "_apply_tenant_env", lambda org: None)
    monkeypatch.setattr(substrate_flow, "setup_logging", lambda **_k: None)

    class _FakeStore:
        def connect(self):
            return True

        def disconnect(self):
            pass

    monkeypatch.setattr(substrate_flow, "LedgerStore", lambda dsn: _FakeStore())
    monkeypatch.setattr(substrate_flow, "_resolve_dsn", lambda: "postgresql://fake")
    # Same nonzero counts on every call — a backlog that already existed
    # before the daemon started, with no NEW data landing during this run.
    monkeypatch.setattr(substrate_flow, "counts", lambda store, org: (131205, 0, 0))

    seen = {}

    def fake_cycle(org, payloads_changed, events_changed, claims_changed):
        seen["args"] = (payloads_changed, events_changed, claims_changed)

    monkeypatch.setattr(substrate_flow, "cycle", fake_cycle)

    substrate_flow.main()

    assert seen["args"] == (True, True, True), \
        "a pre-existing backlog must run on the first cycle, not read as 'unchanged'"
