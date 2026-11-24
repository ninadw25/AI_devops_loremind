"""
domain/analytics/
──────────────────
Aggregate, scope-gated knowledge-continuity analytics computed off the query
hot path: bus-factor / coverage (Continuity Radar) and the offboarding at-risk
worklist. Distinct from domain/analysis.py (the per-query intent analyzer).

All queries here reuse domain/scopes.py::scope_gate on content nodes and treat
Person / Topic / Organization as ungated spine, exactly like domain/retrieval.
"""
