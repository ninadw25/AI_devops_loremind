# lore-ingestion — Deployment (on-prem, on the Neo4j box)

> **Dormant (as of 2026-09).** lore-ingestion is not deployed in prod since the
> CA-firm pivot. This page records how the last deploy worked.

How lore-ingestion is deployed for Loremind, why it's shaped this way, how to
operate it, and how it plugs into the rest of the platform.

## TL;DR

lore-ingestion runs **natively (no Docker) on the same EC2 box as Neo4j**,
supervised by **systemd**. It exposes a control API on `:8100` that
elysia-rbac (deployed as the `auth-service` ECS service) calls to launch ingestion runs. Actual source
ingestion (Slack/Jira/Gmail) runs as `runners.*` subprocesses.

```
loremind-ec2  (m6a.large, x86_64, 15.206.15.12 / priv 10.0.1.247)
├── neo4j                     native systemd service (bolt://localhost:7687)
└── lore-ingestion           native venv + systemd (control API :8100)
        └── talks to Neo4j over localhost; state on the box disk
```

## Why on the box (not Fargate/ECS)

The rest of the platform is ARM64 Fargate + GitOps. lore-ingestion is
deliberately different because co-locating on the Neo4j box dissolves its three
awkward requirements:

- **x86** — the box is x86_64 native (no ARM cross-build; some ingestion deps
  are happier on x86).
- **Persistent state** — watermarks/checkpoints live in `/srv/lore-state` on the
  box's EBS disk (a bind path), so no EFS module work was needed.
- **Daemon supervision + local Neo4j** — systemd supervises it exactly like it
  supervises Neo4j, and ingestion reaches Neo4j over `localhost` (no VPC hop,
  faster bulk Cypher loads).

Trade-off: it shares the box's ~8 GB RAM with Neo4j, so a heavy extraction run
can pressure memory — watch `free -h`, cap concurrency, or resize to
`m6a.xlarge` if it OOMs. And it's **box-managed, not GitOps** (see Updating).

## Filesystem layout (on the box)

| Path | What |
|------|------|
| `/opt/lore-ingestion` | git clone of this repo + `.venv/` |
| `/opt/lore-ingestion/.venv` | Python 3.12 venv (`pip install .`) |
| `/srv/lore/lore.env` | env file with all secrets (chmod 600) |
| `/srv/lore-state` | `LORE_STATE_DIR` — raw/filtered/knowledge/checkpoints/… |
| `/etc/systemd/system/lore-ingestion.service` | the systemd unit |

## The daemon

`systemd` runs the **control API**:

```
ExecStart=/opt/lore-ingestion/.venv/bin/uvicorn control.app:app --host 0.0.0.0 --port 8100
Restart=always            # survives crashes
WantedBy=multi-user.target  # starts on boot
EnvironmentFile=/srv/lore/lore.env
```

Control API surface (`control/app.py`), all gated by `X-Internal-Auth` except
`/health`:

| Route | Purpose |
|-------|---------|
| `GET /health` | liveness (open) |
| `POST /runs` | trigger a run (spawns a `runners.*` subprocess) |
| `GET /runs`, `GET /runs/{id}` | list / inspect runs |
| `POST /runs/{id}/cancel` | cancel a run |

> **The control API currently wires `gmail` (`runners.sync`) and `zoho_mail`
> (`runners.zoho_ledger_sync`) only** (`control/manager.py:_build_command`).
> Slack/Jira/Outlook via `POST /runs` raise `NotImplementedError` — they run through their dedicated
> runners (below) until wired in.

## Configuration (`/srv/lore/lore.env`)

Standard `KEY=VALUE`; pydantic maps `NVIDIA_API_KEY` → `nvidia_api_key`, etc.
Key groups:

```
LORE_STATE_DIR=/srv/lore-state
CONTROL_PORT=8100
CONTROL_INTERNAL_SECRET=<must equal auth-service INGESTION_CONTROL_SECRET>

# LLM extraction — AWS Bedrock
LLM_PROVIDER=bedrock
BEDROCK_API_KEY=...   BEDROCK_REGION=ap-south-1   BEDROCK_MODEL=...

# Embeddings — NVIDIA API
EMBEDDING_PROVIDER=nvidia
NVIDIA_API_KEY=...   (NVIDIA_API_KEY1/2 = rotation pool)

# Neo4j — LOCAL to the box
NEO4J_URI=bolt://localhost:7687      # NOT :7473 (that's the HTTPS browser port)
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<the box's Neo4j password>

# Sources
SLACK_ORG=...  SLACK_BOT_TOKEN=...  SLACK_TOKEN=...  (same value)
SLACK_INGEST_MODE=polling  SLACK_WORKSPACE_DOMAIN=...  SLACK_APP_TIER=marketplace
SLACK_BOT_ALLOWLIST=...
JIRA_BASE_URL=...  JIRA_EMAIL=...  JIRA_API_TOKEN=...  JIRA_TYPE=cloud
```

## How it plugs into the platform (rbac path)

The designed, user-facing trigger path:

```
user (JWT) → api.loremind.in/api/ingestion/*        core-backend (authority-gated)
           → auth-service (elysia-rbac) /internal/ingestion/*   (per-org config in RBAC DB)
           → POST http://10.0.1.247:8100/runs        with X-Internal-Auth: INGESTION_CONTROL_SECRET
           → lore-ingestion daemon → runners.* → Neo4j
```

What makes the last hop work:

- **Networking** — the box SG (`loremind-ec2-sg`, `infra/ec2.tf`) allows inbound
  `:8100` **from the VPC CIDR only** (never public). auth-service's Fargate
  tasks reach the box's private IP directly.
- **URL** — `auth-service` env `INGESTION_CONTROL_URL = http://10.0.1.247:8100`
  (`infra/services.tf`). elysia-rbac reads it in `src/config.ts` and calls
  `${url}/runs` in `src/ingestion/service.ts`.
- **Auth** — the box's `CONTROL_INTERNAL_SECRET` equals auth-service's
  `INGESTION_CONTROL_SECRET` (an SSM SecureString). Verified: no header →
  `401`, correct header → `422`/`202`.

> ⚠️ `10.0.1.247` is the box's **private IP** — stable unless the instance is
> *replaced* (e.g. an AMI change in `ec2.tf`). If replaced, update
> `INGESTION_CONTROL_URL`. A Route53 private record
> (`lore-ingestion.loremind.internal`) would make it a stable name.

The control API is **internal by design** — never exposed publicly; only
elysia-rbac (and the box itself) call it.

## Running ingestion manually

Slack/Jira don't go through the daemon yet — run their runners on the box:

```bash
ssh loremind-ec2
cd /opt/lore-ingestion && set -a && . /srv/lore/lore.env && set +a

# Slack (dry-run, then --apply writes Neo4j)
.venv/bin/python -m runners.slack_reconcile --org techtest --window-days 30
.venv/bin/python -m runners.slack_reconcile --org techtest --window-days 30 --apply

# Jira (needs a valid token + a project key)
.venv/bin/python -m runners.ingest_jira --org techtest --project-key <KEY>

# Gmail (also available via the control API POST /runs)
.venv/bin/python -m runners.sync --org <org> --domain <d> --admin-email <a> --key key.json
```

Ingestion hits paid APIs (Bedrock extraction, NVIDIA embeddings) and can run a
while.

## Operating

```bash
sudo systemctl status lore-ingestion       # state
sudo systemctl restart lore-ingestion      # restart (after env changes)
journalctl -u lore-ingestion -f            # logs
curl -s localhost:8100/health              # health
```

### Updating (manual — no CI yet)

```bash
ssh loremind-ec2
cd /opt/lore-ingestion
git pull
.venv/bin/pip install .        # if deps changed
sudo systemctl restart lore-ingestion
```

### Scheduling (not set up yet)

Ingestion is currently manual. To make it recurring, add a **systemd timer** (or
cron) that runs `slack_reconcile --apply` / `ingest_jira` on an interval — the
lean equivalent of the EventBridge cron the Fargate design used.

## Setup from scratch (reference)

```bash
# 1. build deps + repo
sudo apt-get install -y build-essential python3-dev python3-venv
sudo mkdir -p /opt/lore-ingestion /srv/lore-state /srv/lore
sudo chown -R ubuntu:ubuntu /opt/lore-ingestion /srv/lore-state /srv/lore
git clone <repo> /opt/lore-ingestion

# 2. venv + install (compiles native wheels: hdbscan, tree-sitter, rapidfuzz)
cd /opt/lore-ingestion && python3 -m venv .venv && .venv/bin/pip install .

# 3. env file  (chmod 600 /srv/lore/lore.env)  — see Configuration above

# 4. systemd unit at /etc/systemd/system/lore-ingestion.service — see The daemon
sudo systemctl daemon-reload && sudo systemctl enable --now lore-ingestion
```

## Known gaps / follow-ups

- **Jira token** — the current `JIRA_API_TOKEN` returns `401`; regenerate an
  Atlassian API token before Jira ingestion works.
- **Control API is gmail + zoho_mail only** — wire Slack/Jira into `control/manager.py:_build_command`
  to trigger them via `POST /runs` (today they're runner-only).
- **No CI** — updates are manual (`git pull` + restart). A GitLab Runner on the
  box or an SSM Run-Command deploy job could automate it later.
- **venv bloat** — `sentence-transformers` pulls torch + CUDA (~5 GB) that go
  unused (embeddings use the NVIDIA API); can be trimmed.
- **Secrets on disk** — creds live in `/srv/lore/lore.env`; rotate periodically.
- **LoreDash** — this box isn't an ECS service, so it won't appear in LoreDash's
  Deployments/Logs tabs (its SSM secrets, if any, would still show).
```
