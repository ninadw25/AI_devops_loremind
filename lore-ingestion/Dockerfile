# ─────────────────────────────────────────────────────────────────────
# Lore ingestion — one image, two deployment targets
#
# On-prem:    docker run --env-file lore.env \
#                        -v /srv/lore-state:/app/state \
#                        loremind/lore-ingestion:VERSION \
#                        python -m runners.sync --org ezobooks ...
#
# AWS Fargate: ECS task definition + EventBridge cron. EFS mount at
#              /app/state for persistence. Secrets injected via Secrets
#              Manager into the task's env. Same image.
#
# Build:       docker build -t loremind/lore-ingestion:VERSION .
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# System deps: build tools for native wheels (rapidfuzz, hdbscan,
# tree-sitter*), CA certs for HTTPS to NVIDIA / Gmail / Neo4j Aura.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps FIRST in their own layer — pyproject.toml changes
# rarely, so Docker caches the heavy `pip install` step across most
# rebuilds. Source code changes invalidate only the COPY below.
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

# Source. .dockerignore keeps output/, .venv/, __pycache__, etc. out.
COPY . .

# Persistent state lives under /app/state. On-prem: bind-mount a host
# dir here. Fargate: mount EFS here. The path is fixed in the image so
# the runtime config is just one line in docker-compose / ECS task def.
ENV LORE_STATE_DIR=/app/state
RUN mkdir -p /app/state \
 && mkdir -p /app/state/raw /app/state/filtered /app/state/knowledge \
             /app/state/checkpoints /app/state/logs /app/state/config \
             /app/state/index /app/state/cypher

# Non-root runtime user (security + lint hygiene). UID 1000 because
# that's the convention EFS POSIX permissions are usually configured
# for in the AWS console.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin lore \
 && chown -R lore:lore /app
USER lore

# No ENTRYPOINT/CMD on purpose: the same image runs different stages
# depending on what the scheduler invokes:
#
#   runners.sync          ← 6-hourly hot loop
#   runners.post_process  ← daily cold loop
#   pipeline.<stage>      ← manual backfill / debugging
#
# ECS task definition (or docker-compose) supplies the command.
