"""
control/manager.py
──────────────────
Launches ingestion runs as subprocess jobs over the existing `runners.*`
modules, tracks their state in memory, calls the elysia-rbac
`/internal/ingestion-complete` webhook on success, uploads artifacts to S3,
and notifies the run-status callback so ingestion_run rows are reconciled.

Run lock is backed by per-(org, source) lock files under LORE_STATE_DIR/locks/
so a crashed or restarted instance does not leave ghost in-progress runs
(stale lock files older than MAX_LOCK_AGE_H hours are ignored on acquisition).
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from common.config import get_settings
from common.logger import get_logger
from common.vault_client import VaultProxyError, resolve_run_env
from control.models import RunRecord, RunSpec

log = get_logger("control.manager")

REPO_ROOT = Path(__file__).resolve().parent.parent

# Lock files older than this are considered stale (crashed run) and ignored.
MAX_LOCK_AGE_S = 8 * 3600  # 8 hours

# Control-plane credentials that must never reach a runner subprocess: they are
# cross-tenant authority (vault token) or the control plane's own identity.
_CONTROL_PLANE_SECRETS = frozenset({
    # One token, every tenant's Vault subtree. A connector bug must not become a
    # cross-tenant credential compromise.
    "VAULT_PROXY_TOKEN",
    "CONTROL_INTERNAL_SECRET",
    "RBAC_INTERNAL_SECRET",
})

# Env vars that bind a run to ONE tenant's data — the connector it fetches from
# and the databases it writes to. Stripped from the inherited environment
# whenever vault-proxy is configured (see _child_env).
#
# Vault is then the only permitted source, so an unprovisioned tenant fails on an
# unset variable rather than silently using whatever the host was first pointed
# at: one client's mail fetched with another's token, or written into another's
# graph, under a correct-looking org stamp. resolve_run_env raises on the entries
# it requires; this is the second lock, covering the ones it treats as optional
# (NEO4J_* — the legacy graph the substrate pipeline never writes).
#
# Exact names, not prefixes: SLACK_SCANBACK_DAYS and friends are box-level
# tuning, and sweeping them would silently change first-sync depth. No Gmail
# entry — its auth is a service-account key inlined into the RunSpec
# (gmail_key_content → temp file → --key), not an env var.
_TENANT_SCOPED_ENV = frozenset({
    # Connectors
    "ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN",
    "ZOHO_DATA_CENTER", "ZOHO_ORG_ID", "ZOHO_MAIL_SINGLE_MAILBOX",
    "SLACK_TOKEN", "SLACK_APP_TOKEN",
    "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_OAUTH_TOKEN", "JIRA_PAT",
    # Data stores
    "DATABASE_URL", "POSTGRES_URI",
    "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD",
    "CLAIMS_NEO4J_URI", "CLAIMS_NEO4J_USERNAME", "CLAIMS_NEO4J_PASSWORD",
})


class RunConflict(Exception):
    """A run of this (org, source) is already in progress."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_command(spec: RunSpec, key_path: str | None) -> list[str]:
    """Map a RunSpec to a whitelisted runner invocation."""
    if spec.source == "gmail":
        if not (spec.domain and spec.admin_email and key_path):
            raise ValueError(
                "gmail run requires domain, admin_email, and gmail_key_content",
            )
        cmd = [
            sys.executable, "-m", "runners.sync",
            "--org", spec.org,
            "--domain", spec.domain,
            "--admin-email", spec.admin_email,
            "--key", key_path,
        ]
        if spec.org_domains:
            cmd += ["--org-domains", *spec.org_domains]
        if spec.extra_functional:
            cmd += ["--extra-functional", *spec.extra_functional]
        if spec.data_dir:
            cmd += ["--data-dir", spec.data_dir]
        return cmd

    if spec.source == "zoho_mail":
        # No inline creds: the runner reads ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET /
        # ZOHO_REFRESH_TOKEN from the environment, which _vault_env resolves from
        # the tenant's vault subtree (tenants/{org}/zoho) at launch.
        cmd = [sys.executable, "-m", "runners.zoho_ledger_sync", "--org", spec.org]
        if spec.data_dir:
            cmd += ["--output-dir", spec.data_dir]
        return cmd

    raise NotImplementedError(f"source '{spec.source}' is not wired into the control API yet")


def _lock_dir() -> Path:
    base = Path(os.environ.get("LORE_STATE_DIR", "output"))
    d = base / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _lock_path(org: str, source: str) -> Path:
    safe = f"{org}_{source}".replace("/", "_").replace("..", "_")
    return _lock_dir() / f"{safe}.lock"


def _acquire_lock(org: str, source: str, run_id: str) -> bool:
    """Write a lock file. Returns False if a live lock exists."""
    lp = _lock_path(org, source)
    if lp.exists():
        try:
            age = time.time() - lp.stat().st_mtime
            if age < MAX_LOCK_AGE_S:
                return False
            # Stale lock (crashed run) — remove and continue.
            log.warning("stale_lock_removed path=%s age_s=%d", lp, int(age))
            lp.unlink(missing_ok=True)
        except OSError:
            return False
    try:
        lp.write_text(json.dumps({"run_id": run_id, "ts": time.time()}))
    except OSError as exc:
        log.warning("lock_write_failed path=%s err=%s", lp, exc)
        return False
    return True


def _release_lock(org: str, source: str) -> None:
    _lock_path(org, source).unlink(missing_ok=True)


class RunManager:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._lock = asyncio.Lock()

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list(self, org: str | None = None) -> list[RunRecord]:
        runs = list(self._runs.values())
        if org:
            runs = [r for r in runs if r.org == org]
        return runs

    async def start(self, spec: RunSpec) -> RunRecord:
        run_id = uuid4().hex
        async with self._lock:
            if not _acquire_lock(spec.org, spec.source, run_id):
                raise RunConflict(
                    f"a {spec.source} run for org '{spec.org}' is already in progress",
                )
            rec = RunRecord(
                run_id=run_id,
                org=spec.org,
                source=spec.source,
                trigger=spec.trigger,
                status="queued",
            )
            self._runs[run_id] = rec

        key_path: str | None = None
        try:
            if spec.gmail_key_content:
                key_path = self._write_key_file(spec.gmail_key_content)
            cmd = _build_command(spec, key_path)
            # Data-store creds AND this source's connector secret, both from the
            # tenant's Vault subtree. Raises if either is missing.
            vault_env = await asyncio.to_thread(self._vault_env, spec.org, spec.source)
        except (ValueError, NotImplementedError, VaultProxyError) as exc:
            _release_lock(spec.org, spec.source)
            rec.status = "failed"
            rec.error = str(exc)
            rec.completed_at = _now()
            if key_path:
                self._unlink(key_path)
            raise

        env = self._child_env(spec, vault_env)

        log_path = self._log_path(spec, run_id)
        asyncio.create_task(self._run(run_id, spec, cmd, env, log_path, key_path))
        return rec

    async def cancel(self, run_id: str) -> RunRecord | None:
        rec = self._runs.get(run_id)
        if rec is None:
            return None
        if rec.status in ("succeeded", "failed", "cancelled"):
            return rec
        rec.status = "cancelled"
        proc = self._procs.get(run_id)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
        return rec

    # ── internals ──────────────────────────────────────────────────────

    async def _run(self, run_id: str, spec: RunSpec, cmd, env, log_path, key_path) -> None:
        rec = self._runs[run_id]
        rec.status = "running"
        rec.started_at = _now()
        rec.log_path = str(log_path)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log.info("run_start run_id=%s org=%s source=%s", run_id, spec.org, spec.source)

        logf = open(log_path, "wb")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=logf,
                stderr=asyncio.subprocess.STDOUT,
            )
            self._procs[run_id] = proc
            rc = await proc.wait()
            rec.exit_code = rc
            if rec.status == "cancelled":
                pass
            elif rc == 0:
                rec.status = "succeeded"
            else:
                rec.status = "failed"
                rec.error = f"runner exited with code {rc}"
        except Exception as exc:  # noqa: BLE001
            rec.status = "failed"
            rec.error = f"{type(exc).__name__}: {exc}"
        finally:
            logf.close()
            rec.completed_at = _now()
            self._procs.pop(run_id, None)
            _release_lock(spec.org, spec.source)
            if key_path:
                self._unlink(key_path)

        log.info("run_end run_id=%s status=%s exit=%s", run_id, rec.status, rec.exit_code)
        await self._on_complete(spec, run_id, rec.status, rec.error)

    def _child_env(self, spec: RunSpec, vault_env: dict[str, str]) -> dict[str, str]:
        """Build the runner subprocess environment.

        Four rules, in order:
          1. The runner does NOT inherit the control plane's own credentials.
             VAULT_PROXY_TOKEN would let any runner read every other tenant's
             vault entries; the control/rbac internal secrets would let it
             impersonate the control plane. A connector bug must not become a
             cross-tenant credential compromise.
          2. When vault-proxy is configured, the host's own tenant-scoping
             values (_TENANT_SCOPED_ENV) are stripped too — connector tokens
             AND database URLs.
             Vault is then the only permitted source, so a tenant with no entry
             fails rather than quietly fetching with the credential of whoever
             was onboarded first. Local dev has no proxy URL and keeps reading
             .env exactly as before.
          3. spec.env is applied next, then vault_env over it: the tenant's own
             stored credentials are authoritative over anything a caller put in
             the trigger.
          4. LORE_CONNECTION_ORG is stamped last from spec.org. It is the guard
             `common.config.assert_connection_for_org` checks before any DB
             write; if a caller could set it, it could point a run's databases
             at one tenant while stamping another.
        """
        drop = set(_CONTROL_PLANE_SECRETS)
        if get_settings().vault_proxy_url:
            drop |= _TENANT_SCOPED_ENV
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(spec.env)
        env.update(vault_env)
        env["LORE_CONNECTION_ORG"] = spec.org
        if spec.data_dir:
            env["LORE_STATE_DIR"] = spec.data_dir
        return env

    def _vault_env(self, org: str, source: str | None = None) -> dict[str, str]:
        """Resolve `org`'s DB creds — and this source's connector secret — from
        vault-proxy at launch.

        VAULT_PROXY_URL unset → {} (single-tenant/local: runner inherits the
        server's env exactly as before). Set → the creds MUST resolve;
        VaultProxyError propagates and fails the run — no fallback to the
        server's own env, which may belong to a different client.
        """
        if not get_settings().vault_proxy_url:
            return {}
        env = resolve_run_env(org, source=source)
        log.info("vault_creds_resolved org=%s source=%s keys=%s", org, source, sorted(env))
        return env

    def _write_key_file(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".json", prefix="lore_key_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return path

    @staticmethod
    def _unlink(path: str) -> None:
        try:
            os.unlink(path)
        except OSError:
            pass

    def _log_path(self, spec: RunSpec, run_id: str) -> Path:
        base = Path(spec.data_dir or os.environ.get("LORE_STATE_DIR", "output"))
        return base / "logs" / f"control_run_{run_id}.log"

    async def _on_complete(
        self,
        spec: RunSpec,
        run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        """Called after every terminal run. Order: S3 upload → ingestion-complete webhook → run-status callback."""
        s = get_settings()

        # 1. Upload artifacts to per-org S3 prefix (no-op if bucket unset).
        if status == "succeeded" and s.s3_artifact_bucket:
            from control.artifacts import upload_org_artifacts  # lazy import
            data_dir = spec.data_dir or os.environ.get("LORE_STATE_DIR", "output")
            try:
                n = await asyncio.to_thread(upload_org_artifacts, spec.org, data_dir)
                log.info("s3_upload_done run_id=%s objects=%d", run_id, n)
            except Exception as exc:  # noqa: BLE001
                log.warning("s3_upload_failed run_id=%s error=%s", run_id, exc)

        if not s.rbac_callback_url:
            return

        base = s.rbac_callback_url.rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {s.rbac_internal_secret}",
        }

        # 2. Trigger the artifact loader (existing ingestion-complete webhook).
        if status == "succeeded":
            data_dir = spec.data_dir or os.environ.get("LORE_STATE_DIR", "output")
            complete_payload = json.dumps({"org": spec.org, "runId": run_id, "artifactBase": data_dir}).encode()
            await self._http_post(base + "/internal/ingestion-complete", complete_payload, headers, "ingestion_complete")

        # 3. Close the ingestion_run row in rbac (run-status callback).
        if spec.callbackRunId:
            cb_payload = json.dumps({
                "runId": spec.callbackRunId,
                "status": "succeeded" if status == "succeeded" else "failed",
                **({"error": error} if error else {}),
            }).encode()
            await self._http_post(base + "/internal/ingestion/runs/callback", cb_payload, headers, "run_status_callback")

    async def _http_post(self, url: str, payload: bytes, headers: dict, label: str) -> None:
        def _do() -> int:
            req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                return resp.status
        try:
            http_status = await asyncio.to_thread(_do)
            log.info("%s run_url=%s http=%s", label, url, http_status)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s_failed url=%s error=%s", label, url, exc)
