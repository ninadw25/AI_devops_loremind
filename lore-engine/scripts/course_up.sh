#!/usr/bin/env bash
# One command to bring the Lore engine up locally for the demo. Idempotent.
#   scripts/course_up.sh          # engine from source (uvicorn)
#   scripts/course_up.sh docker   # engine from the Docker image (what EKS runs)
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${VENV:-../.venv}"

docker start lore-neo4j lore-postgres >/dev/null            # data lives in named volumes; see docs/RUN_LOCAL.md
until docker exec lore-postgres pg_isready -U lore -d lore_memory >/dev/null 2>&1; do sleep 2; done
"$VENV/bin/alembic" upgrade head >/dev/null                  # memory.threads / memory.turns

pkill -f 'uvicorn api.main:app' 2>/dev/null || true
docker rm -f lore-engine-course >/dev/null 2>&1 || true
if [ "${1:-}" = docker ]; then
  docker build -q -t lore-engine:course . >/dev/null
  docker run -d --name lore-engine-course --network host --env-file .env lore-engine:course >/dev/null
else
  nohup "$VENV/bin/uvicorn" api.main:app --port 8000 >/tmp/lore-engine.log 2>&1 &
fi
until curl -sf -m 3 localhost:8000/api/health >/dev/null; do sleep 2; done
curl -s localhost:8000/api/health; echo
