# lore-engine

> **Dormant.** lore-engine is not deployed in prod since the CA-firm pivot. It is kept for possible future use.

Query engine for Lore (formerly `lore-backend`). It answers questions over the Neo4j knowledge graph that lore-ingestion builds, serves people analytics (experts, onboarding, offboarding, continuity) and chronograph code-intelligence routes, and stores conversation memory in Postgres.

It is a private service. Its only caller is the elysia core-backend gateway, which authenticates the user, computes content scopes and forwards the request with the shared `X-Internal-Auth` secret. There is no login endpoint. If `INTERNAL_AUTH_SECRET` is unset, every request gets 503.

## Run

```bash
# from lore-engine/, using the workspace venv
../.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
../.venv/bin/python -m pytest tests/ -q
../.venv/bin/alembic upgrade head      # conversation-memory schema
```

gRPC (`lore.engine.v1`) listens on port 50051 when `GRPC_ENABLED=true`.

## Where to read next

- `CLAUDE.md`: layers, key files, env vars, rules.
- `docs/QUERY_PIPELINE_V2.md`: the query pipeline that is serving today.
- `docs/INTEGRATION_ELYSIA.md`, `docs/GRPC_TRANSPORT.md`: the gateway contract (HTTP and gRPC).
- `docs/INTEGRATION_NEO4J.md`, `docs/INTEGRATION_INGESTION.md`: the graph and ingestion contracts.
- `docs/QUERY_PIPELINE_V3.md`, `docs/query_pipeline_improvement.md`, `docs/features/`: proposals that are not built.
