"""
domain/
───────
Pure business logic — no IO, no HTTP, no database calls directly.

Every function here takes explicit arguments (graph client, providers)
and returns typed values. No module-level state, no env reads.
This makes every function unit-testable with plain Python objects.

Sub-packages:
  retrieval/  — three retrieval strategies (person, vector, expansion)

Modules:
  rbac        — role → access groups (RBAC rules)
  context     — format retrieval results into LLM context string
  scoring     — confidence scoring
  sources     — source citation formatting
  prompts     — system prompts and text scrubbers
"""
