"""
pipeline/jira
─────────────
Jira-specific extraction stages.

  deterministic   — field→triple builder (no LLM, structured fields only)
  prose_prompts   — system prompt + thread prompt builder for the LLM prose pass
  identity        — identity emission for the central resolver (P5)
"""
