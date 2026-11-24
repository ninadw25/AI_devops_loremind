FROM python:3.12-slim

# uv — fast, lockfile-driven installs (uv.lock is the source of truth)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps from the lockfile.
# [rerank] = rank-bm25 + flashrank (CPU ONNX). NOT [local-embeddings] — that
# pulls torch + CUDA wheels (~5GB) for a box with no GPU; embeddings use the
# NVIDIA API. [chronograph] = psycopg2 for the vendored chronograph PgStore.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra rerank --extra chronograph
ENV PATH="/app/.venv/bin:$PATH"

# Copy source
COPY . .

# Legacy: CI used to vendor chronograph from lore-ingestion into vendor/. That
# step was dropped 2026-07-03 and chronograph now lives in-repo (chronograph/).
# vendor/ holds only its README today, so this copy only puts that README over
# /app/README.md.
COPY vendor/ /app/

# Default port
ENV PORT=8000

EXPOSE 8000

# Drop root. /app is read-only at runtime (alembic and uvicorn need no writes
# there), but the process does need two writable spots:
#   $HOME      — anything that caches under ~/
#   /opt       — flashrank's DEFAULT cache_dir; it downloads the ONNX reranker
#                model on first Ranker() call, and rerank_provider defaults to
#                flashrank. Without this the first reranked query fails.
RUN useradd --system --uid 10001 --create-home --home-dir /home/lore lore \
    && chown -R lore:lore /app /opt /home/lore
ENV HOME=/home/lore
USER lore

# Apply schema migrations (creates the `memory` schema/tables) before serving.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
