"""
infra
─────
Provider implementations for external services: LLM chat completions
(`infra.llm`) and text embeddings (`infra.embeddings`). Each subpackage
exposes a Protocol + concrete implementations + a registry that the
rest of the codebase resolves through Settings.

Mirrors the layout used by `lore-engine/infra/` so the two
projects keep similar mental models.
"""
