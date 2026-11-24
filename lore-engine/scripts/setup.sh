#!/usr/bin/env bash
# setup.sh — bootstrap lore-engine from scratch
# Usage: bash scripts/setup.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ── Preflight ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "ERROR: docker not found. Install Docker first." >&2
    exit 1
fi
if ! command -v uv &>/dev/null; then
    echo "ERROR: uv not found. Install: curl -Ls https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

# ── PostgreSQL ────────────────────────────────────────────────────────
echo "==> Starting PostgreSQL via Docker Compose..."
docker compose up -d postgres

echo "==> Waiting for PostgreSQL to be healthy..."
until docker compose exec -T postgres pg_isready -U lore -d lore_memory &>/dev/null; do
    printf '.'
    sleep 1
done
echo " ready."

# ── Python environment ────────────────────────────────────────────────
echo "==> Creating Python venv with uv..."
uv venv .venv --clear

echo "==> Installing dependencies..."
uv pip install -e "." --python .venv/bin/python3

# ── Database migrations ───────────────────────────────────────────────
echo "==> Running Alembic migrations..."
.venv/bin/alembic upgrade head

echo ""
echo "==> Setup complete."
echo ""
echo "    PostgreSQL: localhost:5432  DB: lore_memory  User: lore"
echo ""
echo "    Start the API server:"
echo "      .venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload"
echo ""
echo "    Future migrations:"
echo "      .venv/bin/alembic revision --autogenerate -m 'description'"
echo "      .venv/bin/alembic upgrade head"
