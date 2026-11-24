"""Jira API fetch helpers for lore-ingestion.

This module keeps the Jira connector self-contained and lightweight. It uses
`requests` already available in the repo and exposes async `get()` / `post()`
helpers so discovery code can stay async-friendly.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth

from common.config import get_settings
from common.logger import get_logger
from scheduler import RetryPolicy, RetryMode

log = get_logger("jira.fetcher")

# HTTP statuses that mean "this endpoint/API version isn't available here" —
# safe to fall through to the next candidate. Everything else (401/403 auth,
# 5xx server) is a real error that must surface. 429 is handled separately
# (Retry-After backoff + retry) so it no longer crashes the run.
_SKIPPABLE_STATUSES = frozenset({400, 404, 405, 410})


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header into seconds. Supports both the
    delta-seconds form ("120") and the HTTP-date form. Returns None
    when absent or unparseable (caller falls back to computed backoff)."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when is not None:
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        pass
    return None


@dataclass
class JiraFetcher:
    base_url: str
    admin_email: Optional[str] = None
    api_token: Optional[str] = None
    jira_type: str = "cloud"
    oauth_token: Optional[str] = None
    pat: Optional[str] = None
    timeout: int = 60

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

        # Rate-limit / retry governance (shared scheduler primitive).
        # RetryPolicy supplies the backoff curve for 5xx/transport errors;
        # 429 honors the Retry-After header when present. `_request_delay`
        # is a min inter-request spacing applied before every call.
        _s = get_settings()
        self._request_delay: float = max(0.0, _s.jira_request_delay)
        self._max_retries: int = max(0, _s.jira_max_retries)
        self._retry_policy = RetryPolicy(
            max_retries=self._max_retries,
            base_delay=1.0,
            max_delay=60.0,
            mode=RetryMode.EXPONENTIAL,
            jitter=0.2,
        )
        self._last_request_ts: float = 0.0

        if self.oauth_token:
            self._session.headers["Authorization"] = f"Bearer {self.oauth_token.strip()}"
        elif self.admin_email and self.api_token:
            self._session.auth = HTTPBasicAuth(self.admin_email, self.api_token)
        elif self.pat:
            self._session.headers["Authorization"] = f"Bearer {self.pat.strip()}"
        else:
            _s = get_settings()
            env_oauth = _s.jira_oauth_token.strip()
            env_pat = _s.jira_pat.strip()
            env_email = _s.jira_email.strip()
            env_token = _s.jira_api_token.strip()
            if env_oauth:
                self._session.headers["Authorization"] = f"Bearer {env_oauth}"
            elif env_email and env_token:
                self._session.auth = HTTPBasicAuth(env_email, env_token)
            elif env_pat:
                self._session.headers["Authorization"] = f"Bearer {env_pat}"

    def close(self) -> None:
        self._session.close()

    def _throttle(self) -> None:
        """Enforce a minimum spacing between requests (sync, thread-safe
        enough for the connector's single-flight `asyncio.to_thread` use)."""
        if self._request_delay <= 0:
            return
        now = time.monotonic()
        wait = self._request_delay - (now - self._last_request_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_request_ts = time.monotonic()

    def _request(self, method: str, path: str, params: dict | None = None, json_body: Any = None) -> Any:
        """Issue one Jira request with rate-limit + retry governance.

        - 429: honor Retry-After (falls back to computed backoff), then retry.
        - 5xx: exponential backoff + retry.
        - transport errors (timeout/connection): backoff + retry.
        - 2xx/3xx and skippable 4xx (400/404/405/410): returned/raised as-is
          so `_get_first_available` can still slide to the next candidate.

        Only after retries are exhausted does a 429/5xx surface as an error —
        a single 429 no longer crashes the run.
        """
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            self._throttle()
            try:
                resp = self._session.request(
                    method, url, params=params, json=json_body, timeout=self.timeout
                )
            except requests.exceptions.RequestException as exc:
                # Transport-level error (timeout, connection reset, DNS, …).
                last_exc = exc
                if attempt >= self._max_retries:
                    raise
                delay = self._retry_policy.calculate_delay(attempt)
                log.warning(
                    "Jira transport error on %s %s (attempt %d/%d) — retrying in %.1fs: %s",
                    method, path, attempt + 1, self._max_retries + 1, delay, exc,
                )
                time.sleep(delay)
                continue

            status = resp.status_code

            if status == 429:
                if attempt >= self._max_retries:
                    log.error(
                        "Jira rate-limited (429) on %s %s — retries exhausted (%d)",
                        method, path, self._max_retries,
                    )
                    resp.raise_for_status()
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                delay = (
                    retry_after if retry_after is not None
                    else self._retry_policy.calculate_delay(attempt)
                )
                log.warning(
                    "Jira rate-limited (429) on %s %s (attempt %d/%d) — sleeping %.1fs%s",
                    method, path, attempt + 1, self._max_retries + 1, delay,
                    " (Retry-After)" if retry_after is not None else "",
                )
                time.sleep(delay)
                continue

            if status >= 500:
                if attempt >= self._max_retries:
                    log.error(
                        "Jira server error (%d) on %s %s — retries exhausted (%d)",
                        status, method, path, self._max_retries,
                    )
                    resp.raise_for_status()
                delay = self._retry_policy.calculate_delay(attempt)
                log.warning(
                    "Jira server error (%d) on %s %s (attempt %d/%d) — retrying in %.1fs",
                    status, method, path, attempt + 1, self._max_retries + 1, delay,
                )
                time.sleep(delay)
                continue

            # Terminal (success or non-retryable 4xx incl. skippable statuses).
            if status == 204:
                return None
            resp.raise_for_status()
            if "application/json" in resp.headers.get("Content-Type", ""):
                return resp.json()
            return resp.text

        # Loop exhausted without returning (should be unreachable — the last
        # attempt either returns or raises above). Surface the last error.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Jira request failed after retries: {method} {path}")

    async def get(self, path: str, params: dict | None = None) -> Any:
        return await asyncio.to_thread(self._request, "GET", path, params)

    async def post(self, path: str, json_body: Any = None, params: dict | None = None) -> Any:
        return await asyncio.to_thread(self._request, "POST", path, params, json_body)

    async def _get_first_available(self, candidates, *, label: str) -> Any:
        """Try GET candidates in order; return the first success.

        Slides to the next candidate ONLY on a "not available here" status
        (wrong API version / missing endpoint). Auth (401/403), rate-limit
        (429), and server (5xx) errors are re-raised immediately so they
        surface instead of being swallowed into a silent ``None`` that
        downstream reads as "0 results". Raises the last error if every
        candidate is unavailable.
        """
        last_exc: Exception | None = None
        for path, params in candidates:
            try:
                return await self.get(path, params=params)
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in _SKIPPABLE_STATUSES:
                    last_exc = exc
                    continue
                raise
            except Exception as exc:  # transport/network — record and try next
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"No candidate endpoint available for {label}")

    async def list_projects(self) -> Any:
        return await self._get_first_available(
            [
                ("/rest/api/3/project/search", {"status": "live,archived", "maxResults": 1000}),
                ("/rest/api/3/project/search", {"maxResults": 1000}),
                ("/rest/api/3/project", None),
                ("/rest/api/2/project", None),
            ],
            label="list_projects",
        )

    async def fetch_issue(self, issue_key: str) -> Any:
        return await self.get(f"/rest/api/3/issue/{issue_key}", params={"expand": "renderedFields,changelog,comments"})

    async def fetch_issue_comments(self, issue_key: str) -> Any:
        return await self.get(f"/rest/api/3/issue/{issue_key}/comment")

    async def fetch_issue_changelog(self, issue_key: str) -> Any:
        return await self.get(f"/rest/api/3/issue/{issue_key}/changelog")

    async def fetch_issues(
        self, 
        jql: str, 
        start_at: int = 0, 
        max_results: int = 100, 
        fields: str | None = None, 
        expand: str | None = None, 
        next_page_token: str | None = None,
        order_by: str = "created ASC",  # Can be "updated DESC" for reverse-chron
    ) -> Any:
        params: dict = {"jql": jql, "maxResults": max_results}
        if next_page_token:
            params["nextPageToken"] = next_page_token
        else:
            params["startAt"] = start_at
        if fields:
            params["fields"] = fields
        if expand:
            params["expand"] = expand

        payload: dict = {"jql": jql, "maxResults": max_results}
        if next_page_token:
            payload["nextPageToken"] = next_page_token
        else:
            payload["startAt"] = start_at
        if fields:
            payload["fields"] = fields
        if expand:
            payload["expand"] = expand

        attempts = [
            ("GET", "/rest/api/3/search/jql", params, None),
            ("POST", "/rest/api/3/search/jql", None, payload),
            ("GET", "/rest/api/3/search", params, None),
            ("POST", "/rest/api/3/search", None, payload),
            ("GET", "/rest/api/2/search", params, None),
            ("POST", "/rest/api/2/search", None, payload),
        ]

        last_exc: Exception | None = None
        for method, path, query_params, json_body in attempts:
            try:
                if method == "GET":
                    return await self.get(path, params=query_params)
                return await self.post(path, json_body=json_body, params=query_params)
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in _SKIPPABLE_STATUSES:
                    last_exc = exc
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No Jira search endpoint available (tried /search/jql and /search, v3 and v2)")

    async def fetch_issues_reverse(
        self,
        project_key: str,
        checkpoint: dict | None = None,
        anchor_updated: datetime | None = None,
        since_updated: datetime | None = None,
        max_results: int = 100,
    ) -> Any:
        """
        Fetch Jira issues from NOW backwards (reverse-chronological).
        
        Uses JQL ORDER BY updated DESC to get most recently updated issues first.
        Pagination uses nextPageToken from checkpoint for resume.
        
        Args:
            project_key: Jira project key (e.g., "ENG")
            checkpoint: Resume checkpoint with "next_page_token" and "oldest_updated"
            anchor_updated: Upper bound for updated datetime (default: now)
            since_updated: Lower bound for updated datetime (how far back to go)
            max_results: Page size
            
        Returns:
            API response dict with "issues" list and pagination info
        """
        if anchor_updated is None:
            anchor_updated = datetime.now(timezone.utc)
        
        # Build JQL for reverse-chronological fetch
        # Get issues updated between since_updated and anchor_updated, newest first
        jql_parts = [f"project = {project_key}"]
        
        # Add time bounds
        anchor_str = anchor_updated.strftime("%Y-%m-%d %H:%M")
        jql_parts.append(f'updated <= "{anchor_str}"')
        
        if checkpoint and checkpoint.get("oldest_updated"):
            # Resume from where we left off
            since_str = checkpoint["oldest_updated"]
            jql_parts.append(f'updated > "{since_str}"')
        elif since_updated:
            since_str = since_updated.strftime("%Y-%m-%d %H:%M")
            jql_parts.append(f'updated >= "{since_str}"')
        
        # Order by updated DESC (newest first)
        jql = " AND ".join(jql_parts) + " ORDER BY updated DESC"
        
        log.debug(f"Jira reverse-chron fetch: {jql}")
        
        next_token = checkpoint.get("next_page_token") if checkpoint else None
        
        return await self.fetch_issues(
            jql=jql,
            max_results=max_results,
            fields="summary,description,project,reporter,assignee,security,comment,issuetype,status,updated,created",
            expand="changelog",
            next_page_token=next_token,
        )

    async def fetch_project_permission_scheme(self, project_key_or_id: str) -> Any:
        return await self._get_first_available(
            [
                (f"/rest/api/3/project/{project_key_or_id}/permissionscheme", None),
                (f"/rest/api/3/projects/{project_key_or_id}/permissionscheme", None),
                (f"/rest/api/2/project/{project_key_or_id}/permissionscheme", None),
            ],
            label=f"permission scheme for {project_key_or_id}",
        )

    async def fetch_permission_scheme_permissions(self, scheme_id: str) -> Any:
        last_exc: Exception | None = None
        for ep in (
            f"/rest/api/3/permissionscheme/{scheme_id}/permission",
            f"/rest/api/3/permissionscheme/{scheme_id}/permissions",
        ):
            try:
                resp = await self.get(ep)
                if resp:
                    return resp
            except requests.HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                if status in _SKIPPABLE_STATUSES:
                    last_exc = exc
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        return None

    async def fetch_security_schemes(self) -> Any:
        return await self.get("/rest/api/3/issuesecurityschemes")

    async def fetch_security_scheme_levels(self, scheme_id: str) -> Any:
        # Levels are embedded in the scheme detail response, not a /levels sub-resource.
        resp = await self.get(f"/rest/api/3/issuesecurityschemes/{scheme_id}")
        if isinstance(resp, dict) and "levels" in resp:
            return {"values": resp["levels"]}
        return resp

    async def fetch_security_level_members(self, scheme_id: str, level_id: str) -> Any:
        # Correct endpoint: issueSecurityLevelId is a query param, not a path segment.
        try:
            resp = await self.get(
                f"/rest/api/3/issuesecurityschemes/{scheme_id}/members",
                params={"issueSecurityLevelId": level_id, "maxResults": 100},
            )
            if resp:
                return resp
        except Exception as exc:
            log.warning(
                "Security level member fetch failed (scheme=%s level=%s): %s; "
                "this level's grants will be empty.",
                scheme_id, level_id, exc,
            )
        return None

    def discover_fields(self, org: str = "", force: bool = False) -> dict[str, str]:
        config_dir = Path("output/config")
        config_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{org}_" if org else ""
        config_path = config_dir / f"{prefix}jira_field_map.json"
        meta_path = config_dir / f"{prefix}jira_field_map_meta.json"

        if not force and config_path.exists() and meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                discovered_at = datetime.fromisoformat(meta.get("discovered_at"))
                age = datetime.now(timezone.utc) - discovered_at
                if age.days <= 30:
                    return json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        try:
            fields = requests.get(f"{self.base_url}/rest/api/3/field", headers=self._session.headers, auth=self._session.auth, timeout=self.timeout)
            fields.raise_for_status()
            payload = fields.json()
            fmap = {f.get("name"): f.get("id") for f in payload if f.get("custom") and f.get("name") and f.get("id")}
            config_path.write_text(json.dumps(fmap, indent=2), encoding="utf-8")
            meta_path.write_text(json.dumps({"discovered_at": datetime.now(timezone.utc).isoformat()}, indent=2), encoding="utf-8")
            return fmap
        except Exception as exc:
            if config_path.exists():
                try:
                    log.warning(
                        "Jira field discovery failed (%s); falling back to cached "
                        "field map at %s.", exc, config_path,
                    )
                    return json.loads(config_path.read_text(encoding="utf-8"))
                except Exception as cache_exc:
                    log.warning(
                        "Jira field discovery AND cached field map both failed "
                        "(%s); proceeding with NO custom-field map — issues will "
                        "lose Epic/Sprint/custom-field data.", cache_exc,
                    )
                    return {}
            log.warning(
                "Jira field discovery failed (%s) and no cached field map exists; "
                "proceeding with NO custom-field map — issues will lose "
                "Epic/Sprint/custom-field data.", exc,
            )
            return {}
