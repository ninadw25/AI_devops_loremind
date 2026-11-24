"""
control/app.py
──────────────
FastAPI control API for lore-ingestion (docs/HYBRID_DEPLOYMENT.md §5).

Endpoints (all gated by the X-Internal-Auth shared secret):

  Runs — trigger and manage ingestion
    GET    /health                   liveness
    POST   /runs                     trigger a run   → 202 {run_id, status}
    GET    /runs                     list runs (?org=)
    GET    /runs/{run_id}            run status
    POST   /runs/{run_id}/cancel     cancel a running job

  Pipeline observability — the operations dashboard's data source
    GET    /pipeline/{org}           whole-dashboard snapshot (stages, rate, daemons)
    GET    /pipeline/{org}/stages    per-stage done/pending across L0→L3
    GET    /pipeline/{org}/throughput  extraction rate + ETA (measured from the DB)
    GET    /pipeline/{org}/mailboxes per-mailbox fetch state
    GET    /pipeline/{org}/activity  live feed of conversations just extracted
    GET    /pipeline/{org}/claims    recent claims (?kind=)
    GET    /daemons                  which pipeline processes are alive
    GET    /logs                     available log files
    GET    /logs/{name}              tail one log, parsed into levels
    GET    /errors                   errors + warnings across all logs

Run:
    ../.venv/bin/uvicorn control.app:app --host 0.0.0.0 --port 8100
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query

from common.config import get_settings
from common.logger import get_logger
from control import observability
from control.auth import require_internal
from control.manager import RunConflict, RunManager
from control.models import RunAccepted, RunRecord, RunSpec

log = get_logger("control.app")

app = FastAPI(
    title="Lore Ingestion Control API",
    description="Trigger and monitor ingestion runs",
    version="0.1.0",
)

_manager = RunManager()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "lore-ingestion-control"}


@app.post(
    "/runs",
    response_model=RunAccepted,
    status_code=202,
    dependencies=[Depends(require_internal)],
)
async def trigger_run(spec: RunSpec) -> RunAccepted:
    try:
        rec = await _manager.start(spec)
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunAccepted(run_id=rec.run_id, status=rec.status)


@app.get("/runs", response_model=list[RunRecord], dependencies=[Depends(require_internal)])
def list_runs(org: str | None = None) -> list[RunRecord]:
    return _manager.list(org)


@app.get(
    "/runs/{run_id}",
    response_model=RunRecord,
    dependencies=[Depends(require_internal)],
)
def get_run(run_id: str) -> RunRecord:
    rec = _manager.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec


@app.post(
    "/runs/{run_id}/cancel",
    response_model=RunRecord,
    dependencies=[Depends(require_internal)],
)
async def cancel_run(run_id: str) -> RunRecord:
    rec = await _manager.cancel(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run not found")
    return rec


# ── Pipeline observability ──────────────────────────────────────────────────
# All read-only. Every handler is a plain DB read, so these stay responsive
# while fetch and extraction are saturating the machine.

def _guard(fn, *args, **kwargs):
    """Substrate reads fail loudly as 503 (the DB is down / not built yet)
    rather than 500 — the dashboard distinguishes 'not ready' from 'broken'."""
    try:
        return fn(*args, **kwargs)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/pipeline/{org}", dependencies=[Depends(require_internal)])
def pipeline_overview(org: str, source: str = "gmail") -> dict:
    return _guard(observability.overview, org, source)


@app.get("/pipeline/{org}/stages", dependencies=[Depends(require_internal)])
def pipeline_stages(org: str, source: str = "gmail") -> list[dict]:
    return _guard(observability.stage_snapshot, org, source)


@app.get("/pipeline/{org}/throughput", dependencies=[Depends(require_internal)])
def pipeline_throughput(org: str, minutes: int = Query(5, ge=1, le=120)) -> dict:
    return _guard(observability.throughput, org, minutes)


@app.get("/pipeline/{org}/mailboxes", dependencies=[Depends(require_internal)])
def pipeline_mailboxes(org: str, source: str = "gmail") -> list[dict]:
    return _guard(observability.mailboxes, org, source)


@app.get("/pipeline/{org}/activity", dependencies=[Depends(require_internal)])
def pipeline_activity(org: str, limit: int = Query(25, ge=1, le=200)) -> list[dict]:
    return _guard(observability.recent_activity, org, limit)


@app.get("/pipeline/{org}/claims", dependencies=[Depends(require_internal)])
def pipeline_claims(org: str, limit: int = Query(25, ge=1, le=200),
                    kind: str | None = None) -> list[dict]:
    return _guard(observability.recent_claims, org, limit, kind)


@app.get("/daemons", dependencies=[Depends(require_internal)])
def daemons() -> list[dict]:
    return observability.daemon_status()


@app.get("/logs", dependencies=[Depends(require_internal)])
def logs() -> list[dict]:
    return observability.list_logs()


@app.get("/logs/{name}", dependencies=[Depends(require_internal)])
def log_tail(name: str, lines: int = Query(200, ge=1, le=5000)) -> dict:
    try:
        return observability.read_log(name, lines)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/errors", dependencies=[Depends(require_internal)])
def errors(limit: int = Query(60, ge=1, le=500)) -> list[dict]:
    return observability.recent_errors(limit)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("control.app:app", host="0.0.0.0", port=get_settings().control_port)
