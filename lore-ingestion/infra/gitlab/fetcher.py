"""
infra/gitlab/fetcher.py
───────────────────────
GitLab source plugin. Implements the SourcePlugin contract:
  - All auth / rate-limiting / pagination stays inside.
  - Emits NormalizedRecord types only (no direct DB access).

Handles both full and incremental (since=) runs transparently.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.source_records import (
    RepoRef,
    NormalizedCommit,
    NormalizedFileChange,
    NormalizedSourceFile,
    NormalizedMember,
)

log = logging.getLogger("infra.gitlab")

SOURCE_EXTENSIONS = {
    ".ts", ".js", ".tsx", ".jsx", ".py", ".go", ".rs",
    ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".md", ".mdx", ".yaml", ".yml", ".toml",
    ".rb", ".php", ".swift", ".kt", ".scala", ".cs",
}
SKIP_DIRS = {"node_modules", "vendor", ".venv", "__pycache__", "dist", "build", ".next", "public/js"}
MAX_DIFF_COMMITS = 200  # Per-cycle cap for diff fetches


def _should_skip(path: str) -> bool:
    return any(p in SKIP_DIRS for p in path.split("/"))


def _is_source(path: str) -> bool:
    return any(path.endswith(ext) for ext in SOURCE_EXTENSIONS) and not _should_skip(path)


def _build_session(pat: str) -> requests.Session:
    session = requests.Session()
    session.headers["PRIVATE-TOKEN"] = pat
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist={429, 500, 502, 503, 504},
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


class GitLabPlugin:
    """
    GitLab source plugin.

    Environment / constructor:
        base_url        e.g. "https://gitlab.com"
        pat             GitLab personal access token
        project_id      Our internal slug, e.g. "org/repo"
        gitlab_id       GitLab numeric project ID, e.g. "59760967"
    """

    name = "gitlab"

    def __init__(self, base_url: str, pat: str, project_id: str, gitlab_id: str):
        if not pat:
            raise ValueError("GitLab PAT is required")
        if not gitlab_id:
            raise ValueError("gitlab_id (numeric project ID) is required")

        self.base_url = base_url.rstrip("/")
        self.project_id = project_id
        self.gitlab_id = gitlab_id
        self._session = _build_session(pat)

        self.repo = RepoRef(
            source="gitlab",
            repo_url=f"{self.base_url}/{project_id}",
            external_id=gitlab_id,
            full_name=project_id,
        )

    # ── Internal helpers ───────────────────────────────────────────────

    def _get(self, path: str, params: dict = None, timeout: int = 15) -> requests.Response:
        url = f"{self.base_url}/api/v4{path}"
        resp = self._session.get(url, params=params or {}, timeout=timeout)
        self._check_response(resp, url)
        return resp

    @staticmethod
    def _check_response(resp: requests.Response, url: str) -> None:
        if resp.status_code == 401:
            raise PermissionError(f"GitLab 401 Unauthorized for {url}. Check GITLAB_PAT.")
        if resp.status_code == 403:
            raise PermissionError(f"GitLab 403 Forbidden for {url}.")
        if resp.status_code == 404:
            raise FileNotFoundError(f"GitLab 404 Not Found: {url}")
        if resp.status_code >= 400:
            raise RuntimeError(f"GitLab API error {resp.status_code} for {url}: {resp.text[:200]}")

    def _paginate(self, path: str, params: dict = None, sleep: float = 0.25) -> list[dict]:
        """Fetch all pages from a GitLab list endpoint."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results = []
        page = 1
        while True:
            params["page"] = page
            resp = self._get(path, params=params)
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < params["per_page"]:
                break
            page += 1
            if sleep:
                time.sleep(sleep)
        return results

    # ── Plugin interface ───────────────────────────────────────────────

    def list_repos(self) -> list[RepoRef]:
        """This plugin manages exactly one repo (configured at construction)."""
        return [self.repo]

    def fetch_commits(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        ref: str = "main",
    ) -> list[NormalizedCommit]:
        """Return all commits (paginated). `since` enables incremental runs."""
        params: dict = {"ref_name": ref, "with_stats": "true"}
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()

        raw = self._paginate(
            f"/projects/{self.gitlab_id}/repository/commits",
            params=params,
        )
        commits = []
        for c in raw:
            try:
                commits.append(NormalizedCommit(
                    repo=self.repo,
                    sha=c["id"],
                    short_id=c.get("short_id", c["id"][:8]),
                    title=c.get("title", ""),
                    message=c.get("message", ""),
                    author_name=c.get("author_name", ""),
                    author_email=c.get("author_email", ""),
                    authored_date=c["authored_date"],
                    committed_date=c.get("committed_date"),
                    parent_shas=c.get("parent_ids", []),
                    stats=c.get("stats", {}),
                    web_url=c.get("web_url", ""),
                ))
            except Exception as exc:
                log.warning("[gitlab] Skipping malformed commit %s: %s", c.get("id", "?"), exc)
        return commits

    def fetch_commit_files(self, sha: str) -> list[NormalizedFileChange]:
        """Return diffs for one commit. Returns [] on error (non-fatal)."""
        try:
            resp = self._get(
                f"/projects/{self.gitlab_id}/repository/commits/{sha}/diff",
                timeout=30,
            )
        except Exception as exc:
            log.warning("[gitlab] Diff fetch failed for %s: %s", sha[:8], exc)
            return []

        return [
            NormalizedFileChange(
                repo=self.repo,
                commit_sha=sha,
                path=d.get("new_path") or d.get("old_path", ""),
                old_path=d.get("old_path", ""),
                new_file=d.get("new_file", False),
                deleted_file=d.get("deleted_file", False),
                renamed_file=d.get("renamed_file", False),
            )
            for d in resp.json()
        ]

    def fetch_source_files(
        self,
        ref: str = "main",
        paths: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """
        Fetch source files. `paths=None` → full tree fetch.
        Returns {file_path: content}.
        """
        if paths is not None:
            return self._fetch_specific(paths, ref)
        return self._fetch_all(ref)

    def _fetch_all(self, ref: str) -> dict[str, str]:
        tree = self._paginate(
            f"/projects/{self.gitlab_id}/repository/tree",
            params={"ref": ref, "recursive": "true"},
            sleep=0.1,
        )
        source_paths = [f["path"] for f in tree if f["type"] == "blob" and _is_source(f["path"])]
        return self._download_files(source_paths, ref)

    def _fetch_specific(self, paths: list[str], ref: str) -> dict[str, str]:
        filtered = [p for p in paths if _is_source(p)]
        return self._download_files(filtered, ref)

    def _download_files(self, paths: list[str], ref: str) -> dict[str, str]:
        log.info("[gitlab] Fetching %d files from %s", len(paths), self.project_id)
        files: dict[str, str] = {}
        for i, path in enumerate(paths):
            encoded = requests.utils.quote(path, safe="")
            try:
                resp = self._get(
                    f"/projects/{self.gitlab_id}/repository/files/{encoded}/raw",
                    params={"ref": ref},
                    timeout=10,
                )
                files[path] = resp.text
            except Exception as exc:
                log.warning("[gitlab] File fetch failed %s: %s", path, exc)
            if (i + 1) % 50 == 0:
                log.info("[gitlab] Progress: %d/%d", i + 1, len(paths))
                time.sleep(0.5)
        return files

    def fetch_members(self) -> list[NormalizedMember]:
        """Return all project members."""
        raw = self._paginate(f"/projects/{self.gitlab_id}/members/all")
        members = []
        for m in raw:
            access = m.get("access_level", 0)
            if access >= 50:
                role = "owner"
            elif access >= 40:
                role = "maintainer"
            elif access >= 30:
                role = "developer"
            elif access >= 20:
                role = "reporter"
            elif access >= 10:
                role = "guest"
            else:
                role = "unknown"
            members.append(NormalizedMember(
                repo=self.repo,
                login=m.get("username", ""),
                name=m.get("name", ""),
                email=m.get("public_email") or m.get("email", ""),
                role=role,
                avatar_url=m.get("avatar_url", ""),
                web_url=m.get("web_url", ""),
            ))
        return members

    def fetch_project_info(self) -> dict:
        """Fetch project metadata (not part of NormalizedRecord — used to upsert project doc)."""
        resp = self._get(f"/projects/{self.gitlab_id}")
        info = resp.json()
        return {
            "name": info.get("name"),
            "full_name": info.get("path_with_namespace"),
            "description": info.get("description"),
            "visibility": info.get("visibility"),
            "default_branch": info.get("default_branch", "main"),
            "web_url": info.get("web_url"),
            "created_at": info.get("created_at"),
            "source": "gitlab",
            "gitlab_id": self.gitlab_id,
        }
