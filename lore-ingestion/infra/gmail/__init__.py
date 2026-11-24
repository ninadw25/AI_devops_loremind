"""
infra.gmail
───────────
Gmail external-service adapter. Sits alongside `infra/llm/` and
`infra/embeddings/` under the unified `infra/` umbrella for adapters
that talk to outside systems.

Stage 1 of the ingestion pipeline. Entry point: `infra.gmail.run`
(also reachable via the historical `connectors.gmail.run` shim).
"""

__all__ = ["fetcher", "normalizer", "run"]
