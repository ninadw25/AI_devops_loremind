"""
infra/github/fetcher.py
───────────────────────
GitHub source plugin. Full feature parity with GitLabPlugin:
  - Commit fetch with `since` for incremental runs
  - Per-commit diff (file changes)
  - Source file fetch via authenticated Contents API (not raw.githubusercontent.com)
  - Trees API with `truncated` flag detection
  - Member / collaborator fetch
  - Rate-limit handling via X-RateLimit-Remaining + Retry-After
  - Retry on transient 5xx with exponential back-off

All auth / rate-limiting stays inside. No direct DB access.
"""

from __future__ import annotations

import base64
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

log = logging.getLogger("infra.github")

SOURCE_EXTENSIONS = {
    ".ts", ".js", ".tsx", ".jsx", ".py", ".go", ".rs",
    ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".md", ".mdx", ".yaml", ".yml", ".toml",
    ".rb", ".php", ".swift", ".kt", ".scala", ".cs",
}
SKIP_DIRS = {"node_modules", "vendor", ".venv", "__pycache__", "dist", "build", ".next", "public/js"}

GITHUB_API = "https://api.github.com"
RATE_LIMIT_PAUSE_THRESHOLD = 50   # pause when remaining < this
RATE_LIMIT_PAUSE_SECONDS = 60


def _should_skip(path: str) -> bool:
    return any(p in SKIP_DIRS for p in path.split("/"))


def _is_source(path: str) -> bool:
    return any(path.endswith(ext) for ext in SOURCE_EXTENSIONS) and not _should_skip(path)


def _build_session(pat: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"token {pat}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    retry = Retry(
        total=5,
        backoff_factor=1.0,
        status_forcelist={500, 502, 503, 504},
        allowed_methods={"GET"},
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class GitHubPlugin:
    """
    GitHub source plugin.

    Constructor:
        pat     GitHub personal access token (classic or fine-grained)
        owner   Repository owner (user or org name)
        repo    Repository name
    """

    name = "github"

    def __init__(self, pat: str, owner: str, repo: str):
        if not pat:
            raise ValueError("GitHub PAT is required")
        if not owner or not repo:
            raise ValueError("owner and repo are required")

        self.owner = owner
        self.repo_name = repo
        self._session = _build_session(pat)

        full_name = f"{owner}/{repo}"
        self.repo = RepoRef(
            source="github",
            repo_url=f"https://github.com/{full_name}",
            external_id=full_name,
            full_name=full_name,
        )

    # ── Internal helpers ───────────────────────────────────────────────

    def _get(self, path: str, params: dict = None, timeout: int = 20) -> requests.Response:
        if path.startswith("http"):
            url = path
        else:
            url = f"{GITHUB_API}{path}"
        resp = self._session.get(url, params=params or {}, timeout=timeout)
        self._handle_rate_limit(resp)
        self._check_response(resp, url)
        return resp

    def _handle_rate_limit(self, resp: requests.Response) -> None:
        remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
        if remaining < RATE_LIMIT_PAUSE_THRESHOLD:
            reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
            retry_after = int(resp.headers.get("Retry-After", RATE_LIMIT_PAUSE_SECONDS))
            now = time.time()
            wait = max(reset_at - now + 1, retry_after) if reset_at else retry_after
            wait = min(wait, 120)  # cap at 2 min so we don't hang forever
            log.warning(
                "[github] Rate limit low (remaining=%d). Sleeping %.0fs.",
                remaining, wait,
            )
            time.sleep(wait)

    @staticmethod
    def _check_response(resp: requests.Response, url: str) -> None:
        if resp.status_code == 401:
            raise PermissionError(f"GitHub 401 Unauthorized for {url}. Check GITHUB_PAT.")
        if resp.status_code == 403:
            raise PermissionError(f"GitHub 403 Forbidden for {url}. May be rate-limited or no access.")
        if resp.status_code == 404:
            raise FileNotFoundError(f"GitHub 404 Not Found: {url}")
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub API error {resp.status_code} for {url}: {resp.text[:200]}")

    def _paginate(self, path: str, params: dict = None) -> list[dict]:
        """Follow GitHub Link: rel=next pagination."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results = []
        url: Optional[str] = f"{GITHUB_API}{path}" if not path.startswith("http") else path
        while url:
            resp = self._get(url, params=params)
            batch = resp.json()
            if isinstance(batch, list):
                results.extend(batch)
            elif isinstance(batch, dict) and "items" in batch:
                results.extend(batch["items"])
            # Follow pagination via Link header
            link = resp.headers.get("Link", "")
            url = None
            params = {}  # params already encoded in next URL
            for part in link.split(","):
                part = part.strip()
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
        return results

    def _base(self) -> str:
        return f"/repos/{self.owner}/{self.repo_name}"

    # ── Plugin interface ───────────────────────────────────────────────

    def list_repos(self) -> list[RepoRef]:
        return [self.repo]

    def fetch_commits(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        ref: str = "",
    ) -> list[NormalizedCommit]:
        """Return commits (full history or incremental via `since`)."""
        params: dict = {}
        if since:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        if ref:
            params["sha"] = ref

        raw = self._paginate(f"{self._base()}/commits", params=params)
        commits = []
        for c in raw:
            commit = c.get("commit", {})
            author = commit.get("author", {})
            committer = commit.get("committer", {})
            gh_author = c.get("author") or {}
            try:
                commits.append(NormalizedCommit(
                    repo=self.repo,
                    sha=c["sha"],
                    short_id=c["sha"][:8],
                    title=commit.get("message", "").split("\n")[0][:200],
                    message=commit.get("message", ""),
                    author_name=author.get("name", ""),
                    author_email=author.get("email", ""),
                    authored_date=author.get("date", committer.get("date", "")),
                    committed_date=committer.get("date"),
                    parent_shas=[p["sha"] for p in c.get("parents", [])],
                    stats={},  # GitHub /commits list has no per-commit stats
                    web_url=c.get("html_url", ""),
                ))
            except Exception as exc:
                log.warning("[github] Skipping malformed commit %s: %s", c.get("sha", "?")[:8], exc)
        return commits

    def fetch_commit_stats(self, sha: str) -> dict:
        """Fetch additions/deletions for a single commit (not included in list endpoint)."""
        try:
            resp = self._get(f"{self._base()}/commits/{sha}", timeout=15)
            data = resp.json()
            return data.get("stats", {})
        except Exception as exc:
            log.debug("[github] Stats fetch failed for %s: %s", sha[:8], exc)
            return {}

    def fetch_commit_files(self, sha: str) -> list[NormalizedFileChange]:
        """Return file changes for one commit."""
        try:
            resp = self._get(f"{self._base()}/commits/{sha}", timeout=30)
        except Exception as exc:
            log.warning("[github] Commit detail fetch failed for %s: %s", sha[:8], exc)
            return []

        data = resp.json()
        changes = []
        for f in data.get("files", []):
            status = f.get("status", "modified")
            changes.append(NormalizedFileChange(
                repo=self.repo,
                commit_sha=sha,
                path=f.get("filename", ""),
                old_path=f.get("previous_filename", ""),
                new_file=status == "added",
                deleted_file=status == "removed",
                renamed_file=status == "renamed",
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
            ))
        return changes

    def fetch_source_files(
        self,
        ref: str = "",
        paths: Optional[list[str]] = None,
    ) -> dict[str, str]:
        """
        Fetch source files via authenticated Contents API.
        `paths=None` → full tree. Returns {file_path: content}.
        """
        if not ref:
            ref = self._default_branch()
        if paths is not None:
            return self._fetch_specific(paths, ref)
        return self._fetch_all(ref)

    def _default_branch(self) -> str:
        try:
            resp = self._get(f"{self._base()}")
            return resp.json().get("default_branch", "main")
        except Exception:
            return "main"

    def _fetch_all(self, ref: str) -> dict[str, str]:
        """Fetch all source files via Trees API. Handles `truncated` flag."""
        url = f"{self._base()}/git/trees/{ref}?recursive=1"
        try:
            resp = self._get(url, timeout=20)
        except Exception as exc:
            log.error("[github] Tree fetch failed for %s@%s: %s", self.repo.full_name, ref, exc)
            return {}

        data = resp.json()
        if data.get("truncated"):
            log.warning(
                "[github] Tree response truncated for %s (>100k entries). "
                "Fetching root-level tree items only — some files will be missed. "
                "Consider splitting this repo into smaller plugins.",
                self.repo.full_name,
            )
        source_paths = [
            item["path"]
            for item in data.get("tree", [])
            if item.get("type") == "blob" and _is_source(item["path"])
        ]
        return self._download_files(source_paths, ref)

    def _fetch_specific(self, paths: list[str], ref: str) -> dict[str, str]:
        filtered = [p for p in paths if _is_source(p)]
        return self._download_files(filtered, ref)

    def _download_files(self, paths: list[str], ref: str) -> dict[str, str]:
        """Download files via the authenticated Contents API (works for private repos)."""
        log.info("[github] Fetching %d files from %s", len(paths), self.repo.full_name)
        files: dict[str, str] = {}
        for i, path in enumerate(paths):
            try:
                resp = self._get(
                    f"{self._base()}/contents/{path}",
                    params={"ref": ref},
                    timeout=10,
                )
                data = resp.json()
                if data.get("encoding") == "base64":
                    content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
                    files[path] = content
                elif data.get("content"):
                    files[path] = data["content"]
                else:
                    log.debug("[github] Empty content for %s", path)
            except Exception as exc:
                log.warning("[github] File fetch failed %s: %s", path, exc)

            if (i + 1) % 50 == 0:
                log.info("[github] Progress: %d/%d", i + 1, len(paths))
                time.sleep(0.5)

        log.info("[github] Fetched %d/%d files", len(files), len(paths))
        return files

    def fetch_members(self) -> list[NormalizedMember]:
        """Return repo collaborators + org members."""
        members: dict[str, NormalizedMember] = {}

        # Collaborators (includes both org members and outside collaborators)
        try:
            for m in self._paginate(f"{self._base()}/collaborators", params={"affiliation": "all"}):
                login = m.get("login", "")
                if not login:
                    continue
                members[login] = NormalizedMember(
                    repo=self.repo,
                    login=login,
                    name=m.get("name") or login,
                    avatar_url=m.get("avatar_url", ""),
                    web_url=m.get("html_url", ""),
                    role=self._map_github_role(m),
                )
        except PermissionError:
            # Collaborators endpoint needs push access — fall back to contributors
            log.info("[github] No collaborator access for %s, using contributors", self.repo.full_name)

        # Contributors (public, no auth needed)
        try:
            for c in self._paginate(f"{self._base()}/contributors"):
                login = c.get("login", "")
                if not login or login in members:
                    continue
                members[login] = NormalizedMember(
                    repo=self.repo,
                    login=login,
                    name=login,
                    avatar_url=c.get("avatar_url", ""),
                    web_url=c.get("html_url", ""),
                    role="developer",
                )
        except Exception as exc:
            log.warning("[github] Contributors fetch failed: %s", exc)

        # Enrich with user email where possible (rate-limited, best-effort)
        enriched = []
        for login, member in list(members.items())[:50]:  # cap enrichment
            try:
                user_resp = self._get(f"/users/{login}", timeout=5)
                user_data = user_resp.json()
                member = member.model_copy(update={
                    "name": user_data.get("name") or member.name,
                    "email": user_data.get("email") or "",
                })
            except Exception:
                pass
            enriched.append(member)

        return enriched

    @staticmethod
    def _map_github_role(m: dict) -> str:
        perms = m.get("permissions") or m.get("role_name", "")
        if isinstance(perms, dict):
            if perms.get("admin"):
                return "maintainer"
            if perms.get("push"):
                return "developer"
            if perms.get("pull"):
                return "reporter"
        if isinstance(perms, str):
            mapping = {
                "admin": "maintainer", "write": "developer",
                "read": "reporter", "triage": "reporter",
            }
            return mapping.get(perms, "developer")
        return "developer"

    def fetch_pull_requests(self, state: str = "all", limit: int = 500) -> list[dict]:
        """
        Fetch pull requests with author, review, and diff stats.
        Returns normalized dicts ready for upsert_pull_requests().
        """
        raw = self._paginate(
            f"{self._base()}/pulls",
            params={"state": state, "sort": "updated", "direction": "desc"},
        )[:limit]

        prs = []
        for pr in raw:
            user = pr.get("user") or {}
            # Fetch review list (one extra API call per PR — cap at 200 PRs)
            reviews = []
            if len(prs) < 200:
                try:
                    rv = self._paginate(f"{self._base()}/pulls/{pr['number']}/reviews")
                    reviews = [
                        {
                            "reviewer_login": r.get("user", {}).get("login", ""),
                            "state": r.get("state", "COMMENTED"),
                            "submitted_at": r.get("submitted_at"),
                        }
                        for r in rv
                        if r.get("user", {}).get("login")
                    ]
                except Exception:
                    pass

            prs.append({
                "pr_number": pr["number"],
                "title": pr.get("title", ""),
                "body": (pr.get("body") or "")[:4000],
                "state": "merged" if pr.get("merged_at") else pr.get("state", "open"),
                "author_login": user.get("login"),
                "created_at": pr.get("created_at"),
                "merged_at": pr.get("merged_at"),
                "closed_at": pr.get("closed_at"),
                "is_merged": bool(pr.get("merged_at")),
                "labels": [lb["name"] for lb in (pr.get("labels") or [])],
                "comments_count": pr.get("comments", 0) + pr.get("review_comments", 0),
                "changed_files": pr.get("changed_files", 0),
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "html_url": pr.get("html_url", ""),
                "reviews": reviews,
            })
        return prs

    def fetch_issues(self, state: str = "all", limit: int = 500) -> list[dict]:
        """
        Fetch issues (excludes PRs). Returns normalized dicts.
        """
        raw = self._paginate(
            f"{self._base()}/issues",
            params={"state": state, "sort": "updated", "direction": "desc"},
        )[:limit]
        issues = []
        for issue in raw:
            if issue.get("pull_request"):
                continue  # skip PRs listed in issues endpoint
            user = issue.get("user") or {}
            issues.append({
                "issue_number": issue["number"],
                "title": issue.get("title", ""),
                "body": (issue.get("body") or "")[:4000],
                "state": issue.get("state", "open"),
                "author_login": user.get("login"),
                "created_at": issue.get("created_at"),
                "closed_at": issue.get("closed_at"),
                "labels": [lb["name"] for lb in (issue.get("labels") or [])],
                "comments_count": issue.get("comments", 0),
                "html_url": issue.get("html_url", ""),
            })
        return issues

    def fetch_project_info(self) -> dict:
        """Fetch repo metadata for the project doc upsert."""
        resp = self._get(f"{self._base()}")
        info = resp.json()
        return {
            "name": info.get("name"),
            "full_name": info.get("full_name"),
            "description": info.get("description"),
            "visibility": "private" if info.get("private") else "public",
            "default_branch": info.get("default_branch", "main"),
            "web_url": info.get("html_url"),
            "created_at": info.get("created_at"),
            "source": "github",
            "github_id": info.get("full_name"),
        }
