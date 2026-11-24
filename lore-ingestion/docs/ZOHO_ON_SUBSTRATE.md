# Zoho Mail on the L0 Substrate + Credential Wiring

**Date:** 2026-07-23 (creds section revised 2026-08-08) · **Scope delivered:**
creds wiring (UI → Vault → runner)
**and** Zoho ported onto the new L0 substrate, so Zoho mail flows through
claims, dedup, the graph, and the live dashboard exactly like Gmail.

## Why this was two jobs, not one

The new 4-layer substrate (L0 ledger → L1 → L2 → L3 claims → projection) that
the dashboard and all the throughput work are built on was **Gmail-only**:
`runners/ledger_sync.py` hardcoded `SOURCE = "gmail"`. Zoho ran the *old*
JSONL→triples path, invisible to the dashboard. So "get the creds flowing
through all stages" required both credential plumbing **and** an L0 port.

## Piece B — Zoho on L0 (the foundation)

| File | What it does |
|---|---|
| `infra/zoho/l0.py` (new) | Zoho twin of gmail's L0 contract: `canonical_id` / `canonical_payload` / `sighting` / `event_from_payload`. Crucially stamps participant-derived `scope_ids` into `raw` — the fail-closed ACL the graph depends on, which the legacy Zoho normalizer omitted. |
| `runners/l0_sync.py` (new, shared) | The ONE source-agnostic sync loop: per-principal cursor resume, per-batch durable writes, cursor-advance-last, parallel fan-out, failure isolation, totals. No per-source branching. |
| `runners/zoho_ledger_sync.py` (new) | Thin adapter: lists Zoho accounts + a `fetch(account, since_ms, emit)` closure, hands them to `run_l0_sync`. Creds from env, injected at launch from the tenant's Vault subtree. |
| `runners/ledger_sync.py` (Gmail) | Refactored to the same `run_l0_sync` core (236→135 lines); the DWD/since-date/skip-ids specifics live in its fetch closure. Behaviour identical (12 sync tests green). |
| `infra/zoho/fetcher.py` | `fetch_mail_for_account` gained `since_epoch_ms` + `manage_cursor=False` so the L0 runner owns the cursor in Postgres (defaults preserve the old JSONL path). |
| `builders/normalize_events.py` | `_PROJECTORS` gained `"zoho_mail"` → source_payloads project to `events`. |

**Multi-source fix (load-bearing):** `l1_registry.build` and `l2_edges.build`
scanned one source but DELETE-d the whole org — so building for Zoho would have
**wiped Gmail's person spine**. Both now default to scanning **all** of an org's
sources (`source=None`), so the person/org spine MERGES across Gmail + Zoho as
intended. Containers are built per-source. A single `--source` is debug-only.

**Flow daemon** (`runners/substrate_flow.py`) now watches `source_payloads` too:
a new fetch → per-source `normalize → classify_noise → group_conversations`,
which bumps `events` and cascades L1 → L2 → claims → dedup → projection. The
whole river flows from any source's fetch, automatically.

Everything downstream of `events` (noise, grouping, L1, L2, claim extraction,
dedup, projection) reads by `noise_class`/`org`, never by source — so it was
already source-agnostic once Zoho events land.

## Piece A — Credentials (Vault UI → vault-proxy → runner)

Chosen storage: **Vault**, read through the vault-proxy sidecar at run launch.
Nothing per-tenant is at rest in the registry Postgres or in the box's `.env`.

One store for everything per-tenant — data stores and connectors alike:

```
secret/tenants/{org}/neo4j       uri, username, password
secret/tenants/{org}/postgres    uri  (or host/port/user/password/dbname)
secret/tenants/{org}/s3          bucket, region, …
secret/tenants/{org}/zoho        client_id, client_secret, refresh_token,
                                 data_center, [org_id]
```

```
Vault UI (human, today)  →  secret/tenants/expertedge/zoho
  ▲
  │  the credential the client submitted in the dashboard, seeded by hand
  │
elysia-rbac  connectZohoManual → resolveAndVerify (exchanged + verified live)
  │  creates the ingestion_source row (which source, enabled, data_center)
  │  triggerRun → buildRunSpec (NO inline creds; non-secret config→env)
  ▼
control API  POST /runs {source: "zoho_mail"}
  │  _build_command → runners.zoho_ledger_sync
  │  _vault_env(org, "zoho_mail") → resolve_run_env
  ▼
vault-proxy  GET /creds/{org}/neo4j     → NEO4J_*
             GET /creds/{org}/postgres  → DATABASE_URL
             GET /creds/{org}/zoho      → ZOHO_CLIENT_ID / _SECRET / _REFRESH_TOKEN
  ▼
runner  get_settings().zoho_* → get_access_token → Zoho API
```

The secret never rides the trigger: the control plane reads it from Vault at
launch, so it is not on the wire (or in a request log) on every run.

### What goes in `tenants/{org}/zoho`

| Key | Notes |
|---|---|
| `client_id` | the client's OWN self-client app, not Loremind's |
| `client_secret` | same |
| `refresh_token` | an **exchanged** token — Zoho's console emits a one-time `code` that expires in minutes and fails as `invalid_code` |
| `data_center` | `com`/`in`/`eu`/… — sets the accounts/mail hostnames |
| `org_id` | optional; only Cliq's admin-scoped discovery needs it |

Exchange the console `code` once before seeding:

```bash
curl -s -X POST 'https://accounts.zoho.in/oauth/v2/token' \
  -d grant_type=authorization_code -d client_id='1000.XXXX' \
  -d client_secret='XXXX' -d code='1000.XXXX'
```

### Credential types are not enumerated

vault-proxy validates `type` as a slug and reads whatever is at
`<mount>/<prefix>/<tenant>/<type>` (commit `855ea59` replaced the fixed
`s3/postgres/neo4j` allowlist). Its token is scoped to that subtree by the
`tenants-read` policy, so an unknown-but-legal slug reads nothing it was not
already allowed to read. Adding a source is a Vault write plus one
`SOURCE_CRED_ENV` entry in `common/vault_client.py` — no proxy deploy.

| Layer | Change |
|---|---|
| `control/models.py` | `SourceType` += `zoho_mail` |
| `control/manager.py` | `_build_command` zoho branch → `zoho_ledger_sync` (no inline creds); `_child_env` strips tenant-scoping connector vars from the inherited environment whenever `VAULT_PROXY_URL` is set |
| `common/vault_client.py` | `resolve_run_env(org, source)` resolves per-source connector secrets → `ZOHO_*` env; `SOURCE_CRED_ENV` map; `_REQUIRED_CRED_KEYS` |
| `elysia-rbac` schema + model | `ingestion_source_type` enum += `zoho_mail` (migration `0002`); `SourceType` union += `zoho_mail`; `buildRunSpec` zoho branch (config→env only) |
| `lore-frontend` | `ingestionApi`: `zoho_mail` type + `saveZohoSource`; `ZohoConnect.tsx` form at `/connect/zoho` |

### Fail-closed, in two places

A missing Vault entry **raises** and fails the run. There is deliberately no
404 tolerance: an earlier revision swallowed it on the theory that the OAuth
path delivered the token via the run's env, which left a tenant with no entry
running against whatever credential was ambient on the box.

The env-strip in `_child_env` is the second lock. Once `VAULT_PROXY_URL` is set,
`ZOHO_*` / `SLACK_TOKEN` / `JIRA_*` auth are removed from the inherited
environment, so the guarantee does not rest on one function's error handling.
Exact names, not prefixes — `SLACK_SCANBACK_DAYS` and friends are box-level
tuning and stripping them would silently change first-sync depth. Local dev has
no proxy URL and keeps reading `.env`.

## Onboarding today: dashboard → Postgres (staging) → Vault by hand

Vault has no write path, so the credential reaches it via an operator. The
dashboard still does the hard part — exchanging the one-time console `code` and
verifying it against the live Zoho API — and then STAGES the result where a
human can read it:

```
client submits console JSON in the dashboard
  → rbac exchanges code → refresh_token, verifies against Zoho (mailbox count)
  → stored as  inline:{"client_id":…,"client_secret":…,"refresh_token":…}
     in rbac.ingestion_source.credential_secret_ref          ← PLAINTEXT, staging
  → operator reads it, seeds secret/tenants/{org}/zoho in the Vault UI
  → operator CLEARS the staged column
  → runs resolve the credential from Vault, per run, forever after
```

Read it out:

```sql
SELECT org, type, config->>'data_center' AS dc, credential_secret_ref
FROM rbac.ingestion_source WHERE type = 'zoho_mail' AND org = '<org>';
```

Then, once Vault is seeded — this is the last onboarding step, not an optional
tidy-up:

```sql
UPDATE rbac.ingestion_source SET credential_secret_ref = NULL
 WHERE org = '<org>' AND type = 'zoho_mail';
```

Nothing at run time reads that column: `buildRunSpec`'s zoho branch sends only
non-secret config, and the runner takes its credential from Vault. Leaving it
populated is pure standing risk — anyone with read access to the registry has
the client's whole mailbox estate.

`REGISTRY_STAGED_TYPES` in `src/ingestion/service.ts` is what routes a type
here instead of to `storeSecret`. Delete the entry when Vault can be written to
directly; `storeSecret` returns an opaque `sm:` pointer that an operator cannot
copy, which is why the staging path exists at all.

## Prerequisites: a run needs the tenant's WHOLE Vault subtree

`resolve_run_env` fetches `neo4j` and `postgres` on every run, then the source's
own entry. All three must exist or the run fails at launch:

| Entry | Keys | Needed because |
|---|---|---|
| `postgres` | `uri` (or host/port/user/password/dbname) | L0 `source_payloads` lives there — the fetch writes to it directly. The user needs CREATE on the database: `LedgerStore.connect()` builds schema `lore` itself on first run. |
| `neo4j` | `uri`, `password`, `[username]` | **required** — the claim projection (`builders/project_claims.py` → `CLAIMS_NEO4J_*`), the only graph the substrate pipeline writes |
| `zoho` | see above | the connector credential |
| `s3` | — | NOT fetched by `resolve_run_env`; artifact upload reads `S3_ARTIFACT_BUCKET` from the box |

There is only one graph, one Vault entry, one name: `neo4j`. The old
naked-triple ontology (`:Decision`/`:Fact`/`:Task`/`:Event`, built by
`builders/unified_builder.py`) is retired — the substrate pipeline never
called it and no tenant runs it anymore, so there is no second graph to
disambiguate against and no reason for a `neo4j_claims` name to exist.
`resolve_run_env` still populates `CLAIMS_NEO4J_*` internally (that's what
`builders/project_claims.py` reads), but the Vault path a tenant is
provisioned under is just `tenants/<org>/neo4j`.

The up-front Neo4j fetches are deliberate. A Zoho *fetch* touches neither graph,
so they could be deferred — but a tenant whose graphs are not provisioned fails
at projection anyway, and failing at launch names the real cause. Note the
consequence when debugging: vault-proxy 404s on `neo4j` before it ever reads
`zoho`, so a missing graph entry produces an error that reads like a database
problem while you are working on Zoho.

### The daemon needs the same treatment

`runners/substrate_flow.py` runs everything after the fetch (normalize → L1 →
L2 → claims → projection) and is NOT launched by the control API, so it never
passes through `RunManager._child_env`. `_apply_tenant_env(org)` calls
`resolve_run_env(org)` at startup and rewrites `os.environ` from it, so the
daemon's databases are the tenant's. With no `VAULT_PROXY_URL` it leaves the
environment alone (unchanged local behaviour); with one set it refuses to start
rather than starting on the wrong databases.

Per client that is one daemon per org — `substrate_flow --org expertedge`
alongside `--org ezobooks` — each resolving its own credentials.

## Verified

- Python tests pass (Zoho L0 projection ×6, registry/Secrets-Manager credential
  resolution ×14, connector-env isolation ×3, plus the multi-source L1/L2
  change under existing tests).
- rbac + frontend typecheck; frontend builds.
- **Not yet verified live:** the actual Zoho fetch — that needs real Zoho creds
  in the environment or Vault. The moment they're provided:
  `../.venv/bin/python -m runners.zoho_ledger_sync --org <org> --dry-run`
  proves fetch+normalize without writing, then drop `--dry-run` to land it in L0
  and watch the flow daemon carry it to the dashboard.
