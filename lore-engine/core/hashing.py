"""
core/hashing.py
───────────────
Deterministic SHA-256 hashing for stable node ID generation.
Shared by ingestion (Neo4j Cypher) and backend (vector index).
The same text always produces the same hash — pipeline re-runs are safe.
"""

import hashlib


def short_hash(text: str, length: int = 12) -> str:
    """Return the first `length` hex chars of SHA-256(text)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def make_id(source: str, source_id: str) -> str:
    """Stable 16-char ID from a (source, source_id) pair."""
    return hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()[:16]
