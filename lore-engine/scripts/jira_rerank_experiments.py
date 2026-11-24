"""
scripts/jira_rerank_experiments.py
────────────────────────────────────
Experiments for recency signal in rerank_chunks.

Runs three experiments without a live Neo4j:
  1. Score breakdown  — shows per-signal contribution per chunk
  2. Window sensitivity — how rankings change at 30/60/90/180-day windows
  3. Decay shape      — linear vs exponential decay comparison

Usage:
    python scripts/jira_rerank_experiments.py
    python scripts/jira_rerank_experiments.py --experiment breakdown
    python scripts/jira_rerank_experiments.py --experiment window
    python scripts/jira_rerank_experiments.py --experiment decay
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.types import SourcedChunk
from domain.retrieval.jira import _RERANK_STATUS_SCORES


# ── Fake graph (no Neo4j) ─────────────────────────────────────────────

class FakeGraph:
    available = True
    def run(self, cypher: str, **_params) -> list[dict]:
        return []


GRAPH = FakeGraph()
SCOPES = ["jira:project:OPS", "jira:project:ENG"]


# ── Sample dataset ────────────────────────────────────────────────────
# Issues with varied ages, statuses, and base scores to make signal
# differences visible.

def _now_minus(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


SAMPLE_CHUNKS: list[SourcedChunk] = [
    # Recent, active
    SourcedChunk(source="jira", node_id="i1", node_type="Issue",
                 text="[Story ENG-12] Migrate auth to OAuth2\nStatus: In Progress\nAssigned to: Arnav",
                 score=0.75, scope_ids=SCOPES, citations=["ENG-12"],
                 created_at=_now_minus(3)),
    # Recent, done
    SourcedChunk(source="jira", node_id="i2", node_type="Issue",
                 text="[Bug OPS-5] Fix AMI update script\nStatus: Done\nAssigned to: Abhishek",
                 score=0.80, scope_ids=SCOPES, citations=["OPS-5"],
                 created_at=_now_minus(10)),
    # Medium age, high base score
    SourcedChunk(source="jira", node_id="i3", node_type="Issue",
                 text="[Story ENG-7] API rate limiter\nStatus: To Do\nAssigned to: Reia",
                 score=0.85, scope_ids=SCOPES, citations=["ENG-7"],
                 created_at=_now_minus(45)),
    # Old, in progress
    SourcedChunk(source="jira", node_id="i4", node_type="Issue",
                 text="[Epic OPS-2] Infrastructure audit\nStatus: In Progress\nAssigned to: Arnav",
                 score=0.72, scope_ids=SCOPES, citations=["OPS-2"],
                 created_at=_now_minus(80)),
    # Very old, high base score
    SourcedChunk(source="jira", node_id="i5", node_type="Issue",
                 text="[Story ENG-3] Payment gateway integration\nStatus: Done\nAssigned to: Abhishek",
                 score=0.88, scope_ids=SCOPES, citations=["ENG-3"],
                 created_at=_now_minus(120)),
    # No timestamp (pre-fix node)
    SourcedChunk(source="jira", node_id="i6", node_type="Issue",
                 text="[Bug OPS-9] Disk space alert\nStatus: To Do\nAssigned to: Reia",
                 score=0.70, scope_ids=SCOPES, citations=["OPS-9"],
                 created_at=""),
    # Decision node — should pass through unchanged
    SourcedChunk(source="jira", node_id="d1", node_type="Decision",
                 text="Decided to migrate all services to Kubernetes by Q3",
                 score=0.91, scope_ids=SCOPES, citations=[]),
]


# ── Signal computation (mirrors rerank_chunks internals) ─────────────

def _compute_signals(
    chunk: SourcedChunk,
    window_days: int = 90,
    decay_fn: Callable[[float], float] | None = None,
) -> dict:
    """Return per-signal values for a chunk."""
    now = datetime.now(timezone.utc)

    base = float(chunk.get("score", 0.0))

    if chunk.get("node_type") != "Issue":
        return {"base": base, "status": None, "recency": None, "proximity": None,
                "final": base, "note": "non-Issue — not reranked"}

    text = chunk.get("text", "").lower()
    status_score = next(
        (v for kw, v in _RERANK_STATUS_SCORES.items() if f"status: {kw}" in text),
        0.75,
    )

    recency = 0.5
    recency_note = "default (no timestamp)"
    ts = chunk.get("created_at")
    if ts:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            days_old = max(0.0, (now - dt).total_seconds() / 86400)
            if decay_fn:
                recency = decay_fn(days_old)
            else:
                recency = max(0.0, 1.0 - days_old / window_days)
            recency_note = f"{days_old:.0f}d old"
        except ValueError:
            recency_note = "malformed timestamp"

    proximity = 0.0  # no account_id in experiment

    final = base * 0.75 + status_score * 0.10 + recency * 0.05 + proximity * 0.10

    return {
        "base": base,
        "status": status_score,
        "recency": recency,
        "proximity": proximity,
        "final": final,
        "note": recency_note,
    }


# ── Experiment 1: Score breakdown ─────────────────────────────────────

def experiment_breakdown() -> None:
    print("\n" + "=" * 70)
    print("EXPERIMENT 1 — Score breakdown per chunk (window=90d, linear decay)")
    print("=" * 70)

    col = {"id": 6, "node": 10, "base": 7, "status": 8, "recency": 9, "final": 7, "note": 20}
    header = (
        f"{'id':<{col['id']}} {'node_type':<{col['node']}} "
        f"{'base':>{col['base']}} {'status':>{col['status']}} "
        f"{'recency':>{col['recency']}} {'final':>{col['final']}}  note"
    )
    print(header)
    print("-" * 70)

    scored = []
    for c in SAMPLE_CHUNKS:
        sig = _compute_signals(c)
        scored.append((c, sig))

    scored.sort(key=lambda x: x[1]["final"], reverse=True)

    for c, sig in scored:
        if sig["status"] is None:
            print(
                f"{c['node_id']:<{col['id']}} {c['node_type']:<{col['node']}} "
                f"{sig['base']:>{col['base']}.3f} {'—':>{col['status']}} "
                f"{'—':>{col['recency']}}  {sig['final']:>{col['final']}.3f}  {sig['note']}"
            )
        else:
            print(
                f"{c['node_id']:<{col['id']}} {c['node_type']:<{col['node']}} "
                f"{sig['base']:>{col['base']}.3f} {sig['status']:>{col['status']}.2f} "
                f"{sig['recency']:>{col['recency']}.3f}  {sig['final']:>{col['final']}.3f}  {sig['note']}"
            )

    print()
    print("Key: base×0.75 + status×0.10 + recency×0.05 + proximity×0.10")
    print("Observation: check if high base-score old issues (i3, i5) still outrank")
    print("             recent lower-score ones (i1, i6) — if yes, recency weight")
    print("             is too low to be useful.")


# ── Experiment 2: Window sensitivity ─────────────────────────────────

def experiment_window() -> None:
    print("\n" + "=" * 70)
    print("EXPERIMENT 2 — Ranking order at different recency windows")
    print("=" * 70)

    windows = [30, 60, 90, 180]
    issues_only = [c for c in SAMPLE_CHUNKS if c.get("node_type") == "Issue"]

    # Header
    id_col = 6
    w_col = 12
    header = f"{'id':<{id_col}}" + "".join(f"  {w}d window" for w in windows)
    print(header)
    print("-" * (id_col + len(windows) * (w_col + 2)))

    # Compute rank per window
    ranks: dict[int, list[tuple[str, float]]] = {}
    for w in windows:
        scored = []
        for c in issues_only:
            sig = _compute_signals(c, window_days=w)
            scored.append((c["node_id"], sig["final"]))
        scored.sort(key=lambda x: x[1], reverse=True)
        ranks[w] = scored

    # Print rank position per chunk per window
    for c in issues_only:
        nid = c["node_id"]
        row = f"{nid:<{id_col}}"
        for w in windows:
            rank = next(i + 1 for i, (n, _) in enumerate(ranks[w]) if n == nid)
            score = next(s for n, s in ranks[w] if n == nid)
            row += f"  #{rank} ({score:.3f})  "
        print(row)

    print()
    print("Observation: if rank order is identical across all windows, the recency")
    print("             signal has no meaningful effect at current data ages.")
    print("             Large rank shifts indicate the window choice matters.")


# ── Experiment 3: Decay shape ─────────────────────────────────────────

def experiment_decay() -> None:
    print("\n" + "=" * 70)
    print("EXPERIMENT 3 — Linear vs exponential decay shape")
    print("=" * 70)

    half_life = 30  # days — issue loses half its recency score every 30 days
    exp_decay: Callable[[float], float] = lambda d: math.exp(-d / half_life)

    issues_only = [c for c in SAMPLE_CHUNKS if c.get("node_type") == "Issue"]

    col = {"id": 6, "lin_rec": 12, "exp_rec": 12, "lin_final": 12, "exp_final": 12}
    header = (
        f"{'id':<{col['id']}}  {'linear rec':>{col['lin_rec']}}  "
        f"{'exp rec':>{col['exp_rec']}}  {'linear final':>{col['lin_final']}}  "
        f"{'exp final':>{col['exp_final']}}"
    )
    print(header)
    print("-" * 60)

    lin_scored = []
    exp_scored = []

    for c in issues_only:
        lin = _compute_signals(c, window_days=90)
        exp = _compute_signals(c, decay_fn=exp_decay)
        lin_scored.append((c["node_id"], lin))
        exp_scored.append((c["node_id"], exp))
        print(
            f"{c['node_id']:<{col['id']}}  "
            f"{lin['recency']:>{col['lin_rec']}.3f}  "
            f"{exp['recency']:>{col['exp_rec']}.3f}  "
            f"{lin['final']:>{col['lin_final']}.3f}  "
            f"{exp['final']:>{col['exp_final']}.3f}"
        )

    lin_order = [n for n, _ in sorted(lin_scored, key=lambda x: x[1]["final"], reverse=True)]
    exp_order = [n for n, _ in sorted(exp_scored, key=lambda x: x[1]["final"], reverse=True)]

    print()
    print(f"Linear rank order:      {' > '.join(lin_order)}")
    print(f"Exponential rank order: {' > '.join(exp_order)}")
    changed = lin_order != exp_order
    print(f"Rank order changed:     {'YES — decay shape matters' if changed else 'NO — same order'}")
    print()
    print(f"Exponential half-life={half_life}d means an issue {half_life}d old scores")
    print(f"recency={exp_decay(half_life):.3f} vs linear {max(0, 1 - half_life/90):.3f}")
    print("Exponential penalises old issues more aggressively in early weeks.")


# ── Entrypoint ────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--experiment", default="all",
        choices=["all", "breakdown", "window", "decay"],
        help="Which experiment to run (default: all)",
    )
    args = p.parse_args()

    if args.experiment in ("all", "breakdown"):
        experiment_breakdown()
    if args.experiment in ("all", "window"):
        experiment_window()
    if args.experiment in ("all", "decay"):
        experiment_decay()


if __name__ == "__main__":
    main()
