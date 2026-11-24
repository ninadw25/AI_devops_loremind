#!/usr/bin/env bash
# One command for the whole demo: Neo4j + Postgres + engine (:8000) + Ask Lore UI (:5173).
#   scripts/demo_up.sh          start everything, then tail until Ctrl-C stops the UI
#   scripts/demo_up.sh stop     stop engine and UI (databases keep running)
# Login: demo@lore.local / Lore@Demo2026   (demo-only account, see lore-frontend/app/src/services/authApi.ts)
set -euo pipefail
cd "$(dirname "$0")/.."
ENGINE_DIR=$PWD
UI_DIR="$ENGINE_DIR/../lore-frontend/app"
VENV="${VENV:-$ENGINE_DIR/../.venv}"

if [ "${1:-}" = stop ]; then
  fuser -k 8000/tcp 5173/tcp 2>/dev/null || true
  echo "engine and UI stopped"; exit 0
fi

# 1. python deps the engine needs on a fresh venv (pins: see pyproject.toml)
"$VENV/bin/python" -c "import flashrank, psycopg2, pgvector" 2>/dev/null \
  || uv pip install --python "$VENV/bin/python" -q "flashrank>=0.2.9" psycopg2-binary pgvector "sqlalchemy[asyncio]>=2.0,<2.1"

# 2. databases (data lives in named docker volumes) and the engine
docker start lore-neo4j lore-postgres >/dev/null
until (echo > /dev/tcp/localhost/7687) 2>/dev/null; do sleep 2; done   # bolt port open; course_up.sh then waits for engine health
grep -q '^ANALYSIS_TIMEOUT_MS=' .env || echo "ANALYSIS_TIMEOUT_MS=4000" >> .env
fuser -k 8000/tcp 2>/dev/null || true
scripts/course_up.sh >/tmp/course_up.log 2>&1   # starts engine after Neo4j is ready, so health is ok

# 3. UI
[ -e "$UI_DIR/node_modules" ] || { [ -d "$ENGINE_DIR/../../lore-demo/lore-frontend/app/node_modules" ] \
  && ln -s "$ENGINE_DIR/../../lore-demo/lore-frontend/app/node_modules" "$UI_DIR/node_modules" \
  || (cd "$UI_DIR" && npm install --silent); }
fuser -k 5173/tcp 2>/dev/null || true
cd "$UI_DIR"
nohup npm run dev -- --host 127.0.0.1 --port 5173 >/tmp/lore-ui.log 2>&1 &
until curl -sf -o /dev/null http://127.0.0.1:5173/; do sleep 1; done

cat <<MSG

  Lore demo is up
  ───────────────────────────────────────────────
  App          http://127.0.0.1:5173         (use 127.0.0.1 or localhost)
  Login        demo@lore.local
  Password     Lore@Demo2026
  Engine API   http://localhost:8000/docs
  Neo4j        http://localhost:7474   (user neo4j, password = NEO4J_PASSWORD in .env)
  Stop         scripts/demo_up.sh stop
MSG
