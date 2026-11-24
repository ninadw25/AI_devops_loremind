"""
infra/
──────
External service adapters. Each module owns exactly one dependency.

Packages:
  llm/           Chat providers (NVIDIA, OpenAI, OpenRouter) + registry
  embeddings/    Embedding providers + registry
  db/            SQLAlchemy ORM models (memory + knowledge graph)
  neo4j_client   Neo4j driver lifecycle
  postgres_client SQLAlchemy async engine + session factory (pgvector codec registered)
  pg_knowledge_repository  PostgresKnowledgeRepository — all GraphOp → SQL translations

Rule: infra/ imports only from core/. Never from domain/, services/, api/.
"""
