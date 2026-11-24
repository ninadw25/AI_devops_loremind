# Integration: lore-engine ↔ Neo4j

How the query engine connects to and reads the knowledge graph. lore-engine is a
**read-only consumer** of Neo4j; lore-ingestion is the only writer.

---

## 1. What's already in place

`infra/neo4j_client.py::Neo4jClient` owns the driver:

- One driver per process, built in `api/main.py:lifespan` from settings, stored
  on `app.state.graph`, injected via `Depends`. Closed on shutdown.
- `run(cypher, **params) -> list[dict] | None`. Returns `None` (not an
  exception) when the DB is unavailable or a query errors, so callers degrade
  gracefully.
- `available` property → `False` when `NEO4J_URI` is unset or connectivity
  fails. The query pipeline checks this and returns empty rather than crashing.
- All Cypher lives in `domain/retrieval/*` (never in the client); the scope gate
  is the single-owner fragment from `domain/scopes.py`.

So "connecting to Neo4j" is purely **configuration + provisioning** — no new
code is needed in lore-engine.

## 2. Configuration

Read through `core/settings.py` (the only place env is read):

```bash
NEO4J_URI=neo4j+s://<host>:7687        # Aura: neo4j+s://xxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<password>
```

URI scheme by deployment:
| Deployment | Scheme | Notes |
|---|---|---|
| **Neo4j Aura** (managed) | `neo4j+s://` | TLS + routing; the `+s` is required |
| Self-hosted cluster | `neo4j://` | routing, add `+s`/`+ssc` for TLS |
| Single instance / local | `bolt://` | no routing; `bolt+s://` for TLS |
| Bolt port | `7687` | `7474` (HTTP) / `7473` (HTTPS) are the **browser**, not bolt — do not use them in `NEO4J_URI` |

> The audit script tolerates a schemeless `host:port` by prepending `neo4j://`,
> but set the scheme explicitly in real configs.

## 3. What lore-engine expects in the graph (read contract)

These are produced by lore-ingestion (see `INTEGRATION_INGESTION.md`). If any is
missing/mismatched, retrieval silently returns less — not an error.

1. **Vector index `knowledge_embeddings`** over the `embedding` property of
   `:KnowledgeNode`. Both engines call
   `db.index.vector.queryNodes('knowledge_embeddings', k, $embedding)`.
   - **Embedding model + dimension MUST match** what lore-engine queries with
     (`EMBEDDING_PROVIDER`/`EMBEDDING_MODEL`/`EMBEDDING_DIM`, default NVIDIA NeMo
     2048-dim). A query vector of the wrong model/dim returns garbage neighbours
     or errors. This is the single most important cross-repo invariant.
2. **Node id constraints** — each content type has a unique-constrained id
   (`decision_id`/`task_id`/`fact_id`/`event_id`/`issue_id`) and spine types
   (`person_id`/`org_id`/`topic_id`). Frontier traversal matches per-type on
   these (indexed). No unified `node_id` property exists by design.
3. **ACL properties** — `scope_ids` (list) and optional `scope_all_ids` on
   content nodes. Empty/absent → invisible under the fail-closed gate.
4. **Edges** — the ontology relations (`DECIDED`, `BECAUSE`, `REGARDING`,
   `STATED_BY`, `ASSIGNED_TO`, `PARTICIPATED_IN`, `CORROBORATES`,
   `COMMUNICATES_WITH`; Jira `DERIVED_FROM`, `ASSIGNED_TO`, `REPORTED_BY`,
   `IN_SPRINT`, `HAS_COMPONENT`).

## 4. Database user & permissions

Provision a **read-only** Neo4j user for lore-engine:

```cypher
CREATE USER lore_engine SET PASSWORD '…' CHANGE NOT REQUIRED;
GRANT ROLE reader TO lore_engine;     -- or a custom role with MATCH on the graph
```

lore-engine issues only `MATCH` / `CALL db.index.vector.queryNodes` — no writes.
A reader role both follows least-privilege and guarantees the engine can never
corrupt the graph ingestion owns. (Ingestion uses its own read-write user.)

## 5. Connection pooling & resilience

- The Neo4j Python driver pools connections internally; one `Neo4jClient` per
  process is correct — do not create a driver per request.
- For multiple lore-engine replicas, each holds its own driver/pool; Aura/
  cluster routing handles load. Tune `max_connection_pool_size` only if you see
  pool exhaustion under high concurrency (would be added in `Neo4jClient`).
- Transient connectivity loss → `run()` returns `None` → empty results +
  `graph_available=False` in `/api/query/health`. The engine does **not** crash;
  it reports degraded. Add an alert on `neo4j_unavailable` log events.

## 6. Readiness gate before relying on results

Run the audit against the live graph (it's read-only):

```bash
NEO4J_URI=… NEO4J_USERNAME=… NEO4J_PASSWORD=… \
  ../.venv/bin/python scripts/audit_scope_coverage.py
```

It reports: vector index present, per-label scope/embedding coverage, source
breakdown (by scope prefix), and confirms the per-type id model. Gate cutover on
**≥99% of Gmail+Slack content nodes carrying non-empty `scope_ids`** — otherwise
fail-closed ACL makes unscoped nodes vanish and it looks like broken recall.

## 7. Network

- Neo4j Aura is reached over the public internet on `7687` with TLS
  (`neo4j+s://`) — restrict by Aura's IP allowlist to the lore-engine subnet.
- Self-hosted: keep Neo4j on the private network; only lore-engine (read) and
  lore-ingestion (write) reach it. Never expose bolt publicly without TLS + auth.
