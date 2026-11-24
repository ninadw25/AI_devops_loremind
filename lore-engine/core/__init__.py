"""
core/
─────
Foundation layer — no dependencies on other lore-engine packages.
All other layers import from here; this package imports from nothing internal.

Exports:
  settings   — Pydantic Settings, single source of all configuration
  logging    — structured JSON logger factory + correlation context
  observability — Langfuse client singleton with safe no-op fallback
  exceptions — LoreError hierarchy
  types      — shared dataclasses (UserContext, Source, KnowledgeNode)
  hashing    — deterministic SHA-256 node ID generation
"""
