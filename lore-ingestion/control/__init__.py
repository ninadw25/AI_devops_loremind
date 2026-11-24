"""
control/
────────
Thin HTTP control plane for lore-ingestion (docs/HYBRID_DEPLOYMENT.md §5).

lore-ingestion is a batch ETL with no inbound surface; this package adds a
FastAPI server that lets the control plane (elysia-rbac scheduler / elysia-backend
super-admin surface) trigger ingestion runs and query their status. Heavy work is
NOT done in the request — each run is launched as a subprocess job over the
existing `runners.*` modules, so pipeline logic is unchanged.

Run:
    ../.venv/bin/uvicorn control.app:app --host 0.0.0.0 --port 8100
"""
