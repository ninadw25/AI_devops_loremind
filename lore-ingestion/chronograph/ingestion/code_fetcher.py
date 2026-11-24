"""
Code fetcher — fetches actual source code from GitLab/GitHub API.

Reads the repo tree to find source files, then fetches each file's content
via API and stores in PostgreSQL (or returns for further processing).
"""

import logging
import time
from typing import Optional

import requests

log = logging.getLogger("chronograph.code_fetcher")

SOURCE_EXTENSIONS = {
    ".ts", ".js", ".tsx", ".jsx", ".py", ".go", ".rs",
    ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".md", ".mdx", ".yaml", ".yml", ".toml",
    ".rb", ".php", ".swift", ".kt", ".scala", ".cs",
}

SKIP_DIRS = {"node_modules", "vendor", ".venv", "__pycache__", "dist", "build", ".next", "public/js"}


def _should_skip(path: str) -> bool:
    parts = path.split("/")
    return any(p in SKIP_DIRS for p in parts)


def _is_source(path: str) -> bool:
    return any(path.endswith(ext) for ext in SOURCE_EXTENSIONS) and not _should_skip(path)


class GitLabCodeFetcher:
    """Fetch source code from GitLab API v4."""

    def __init__(self, base_url: str, pat: str, project_id: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {"PRIVATE-TOKEN": pat}
        self.project_id = project_id

    def fetch_repo_tree(self, ref: str = "main") -> list[dict]:
        """Get full repository file tree."""
        all_items = []
        page = 1
        per_page = 100
        while True:
            url = f"{self.base_url}/api/v4/projects/{self.project_id}/repository/tree"
            resp = requests.get(url, headers=self.headers, params={
                "ref": ref, "recursive": "true", "per_page": per_page, "page": page,
            }, timeout=15)
            if resp.status_code != 200:
                log.error("[code_fetcher] Tree fetch failed: %s", resp.status_code)
                break
            items = resp.json()
            if not items:
                break
            all_items.extend(items)
            page += 1
        return all_items

    def fetch_source_files(self, ref: str = "main", tree: list[dict] = None) -> dict[str, str]:
        """
        Fetch all source files from the repo.
        Returns {file_path: content}.
        """
        if tree is None:
            tree = self.fetch_repo_tree(ref)

        source_paths = [f["path"] for f in tree if f["type"] == "blob" and _is_source(f["path"])]
        log.info("[code_fetcher] Fetching %d source files from %s", len(source_paths), self.project_id)

        files = {}
        for i, path in enumerate(source_paths):
            encoded = requests.utils.quote(path, safe="")
            url = f"{self.base_url}/api/v4/projects/{self.project_id}/repository/files/{encoded}/raw"
            try:
                resp = requests.get(url, headers=self.headers, params={"ref": ref}, timeout=10)
                if resp.status_code == 200:
                    files[path] = resp.text
                else:
                    log.warning("[code_fetcher] %s: HTTP %s", path, resp.status_code)
            except Exception as e:
                log.warning("[code_fetcher] %s: %s", path, e)

            # Rate limiting
            if (i + 1) % 50 == 0:
                log.info("[code_fetcher] Progress: %d/%d files", i + 1, len(source_paths))
                time.sleep(1)

        log.info("[code_fetcher] Fetched %d/%d source files", len(files), len(source_paths))
        return files

    def fetch_specific_files(self, file_paths: list[str], ref: str = "main") -> dict[str, str]:
        """
        Fetch a specific list of source files from the repo.
        Returns {file_path: content}.
        """
        log.info("[code_fetcher] Fetching %d specific files from %s", len(file_paths), self.project_id)
        files = {}
        for i, path in enumerate(file_paths):
            if not _is_source(path):
                continue
            encoded = requests.utils.quote(path, safe="")
            url = f"{self.base_url}/api/v4/projects/{self.project_id}/repository/files/{encoded}/raw"
            try:
                resp = requests.get(url, headers=self.headers, params={"ref": ref}, timeout=10)
                if resp.status_code == 200:
                    files[path] = resp.text
                else:
                    log.warning("[code_fetcher] %s: HTTP %s", path, resp.status_code)
            except Exception as e:
                log.warning("[code_fetcher] %s: %s", path, e)
            
            if (i + 1) % 50 == 0:
                log.info("[code_fetcher] Progress: %d/%d files", i + 1, len(file_paths))
                time.sleep(1)

        log.info("[code_fetcher] Fetched %d/%d specific files", len(files), len(file_paths))
        return files


class GitHubCodeFetcher:
    """Fetch source code from GitHub API v3."""

    def __init__(self, pat: str, owner: str, repo: str):
        self.headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.v3+json",
        }
        self.owner = owner
        self.repo = repo

    def fetch_repo_tree(self, ref: str = "main") -> list[dict]:
        """Get full repository file tree via GitHub Trees API."""
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{ref}?recursive=1"
        resp = requests.get(url, headers=self.headers, timeout=15)
        if resp.status_code != 200:
            log.error("[code_fetcher] GitHub tree fetch failed: %s %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json()
        return [
            {"path": item["path"], "type": "blob" if item["type"] == "blob" else "tree"}
            for item in data.get("tree", [])
        ]

    def fetch_source_files(self, ref: str = "main", tree: list[dict] = None) -> dict[str, str]:
        """Fetch all source files. Returns {path: content}."""
        if tree is None:
            tree = self.fetch_repo_tree(ref)

        source_paths = [f["path"] for f in tree if f["type"] == "blob" and _is_source(f["path"])]
        log.info(
            "[code_fetcher] Fetching %d source files from %s/%s",
            len(source_paths), self.owner, self.repo,
        )

        files = {}
        for i, path in enumerate(source_paths):
            url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{ref}/{path}"
            try:
                resp = requests.get(url, headers=self.headers, timeout=10)
                if resp.status_code == 200:
                    files[path] = resp.text
                else:
                    log.warning("[code_fetcher] %s: HTTP %s", path, resp.status_code)
            except Exception as e:
                log.warning("[code_fetcher] %s: %s", path, e)

            if (i + 1) % 50 == 0:
                log.info("[code_fetcher] Progress: %d/%d files", i + 1, len(source_paths))
                time.sleep(1)

        log.info("[code_fetcher] Fetched %d/%d source files", len(files), len(source_paths))
        return files

    def fetch_specific_files(self, file_paths: list[str], ref: str = "main") -> dict[str, str]:
        """Fetch a specific list of source files. Returns {path: content}."""
        log.info(
            "[code_fetcher] Fetching %d specific files from %s/%s",
            len(file_paths), self.owner, self.repo,
        )
        files = {}
        for i, path in enumerate(file_paths):
            if not _is_source(path):
                continue
            url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{ref}/{path}"
            try:
                resp = requests.get(url, headers=self.headers, timeout=10)
                if resp.status_code == 200:
                    files[path] = resp.text
                else:
                    log.warning("[code_fetcher] %s: HTTP %s", path, resp.status_code)
            except Exception as e:
                log.warning("[code_fetcher] %s: %s", path, e)
            if (i + 1) % 50 == 0:
                time.sleep(1)
        log.info("[code_fetcher] Fetched %d/%d specific files", len(files), len(file_paths))
        return files
