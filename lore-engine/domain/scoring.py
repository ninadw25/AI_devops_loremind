"""
domain/scoring.py
─────────────────
Confidence scoring for query results.

Pure function — takes counts, returns a label string.
"""

from __future__ import annotations

from core.types import RetrievalResult, SourcedChunk


def score_confidence(result: RetrievalResult) -> str:
    """Return 'high' | 'medium' | 'low' based on retrieval depth."""
    total = len(result.primary) + len(result.supporting)
    if total >= 5 or len(result.primary) >= 3:
        return "high"
    if total >= 2 or result.persons:
        return "medium"
    return "low"


def score_confidence_chunks(chunks: list[SourcedChunk]) -> str:
    """Confidence from the merged, ranked result set: how much top-ranked,
    scope-gated content survived, and whether it spans more than one source
    (cross-source agreement is a strong signal).

    - high:   a solid result set, or corroboration across multiple sources
    - medium: some relevant content
    - low:    little or nothing
    """
    n = len(chunks)
    if n == 0:
        return "low"
    top_score = max((float(c.get("score", 0.0)) for c in chunks), default=0.0)
    sources = {c.get("source") for c in chunks}
    multi_source = len(sources) >= 2

    if (n >= 4 and top_score >= 0.5) or (multi_source and n >= 3):
        return "high"
    if n >= 2 or top_score >= 0.4:
        return "medium"
    return "low"
