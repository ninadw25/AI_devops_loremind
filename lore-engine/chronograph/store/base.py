"""
chronograph/store/base.py
─────────────────────────
Re-exports the Store Protocol for backward compatibility.
Prefer importing from chronograph.store.store directly.
"""

from chronograph.store.store import Store as ChronographStore

__all__ = ["ChronographStore"]
