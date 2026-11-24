"""
Full ingestion pipeline — fetches data from GitLab/GitHub API, parses code,
generates embeddings, stores in the configured store backend.

Steps:
  1. FETCH  — project info, commits, members, commit_files from API
  2. CODE   — fetch actual source code from API
  3. PARSE  — tree-sitter extracts symbols (functions, classes, complexity)
  4. EMBED  — generate embeddings via OpenRouter, store in DB
"""

import logging
import time
import json
import requests
from pathlib import Path
from typing import Optional
from datetime import datetime

from chronograph.ingestion.code_fetcher import GitLabCodeFetcher, GitHubCodeFetcher
from chronograph.parsing.parser import parse_file_content
from chronograph.parsing.symbol_extractor import extract_symbols
from chronograph.diffing.change_classifier import classify_commit

log = logging.getLogger("chronograph.ingest_pipeline")


class IngestPipeline:
    """End-to-end ingestion: API → store (with code + symbols + embeddings)."""

    def __init__(self, store, config: dict):
        self.store = store
        self.config = config

    async def ingest_gitlab_project(
        self,
        project_id: str,
        gitlab_project_id: str,
        ref: str = "main",
        since: Optional[datetime] = None,
    ) -> dict:
        """
        Full ingestion for a GitLab project.
        project_id: our internal ID (e.g. "makarandKate/gmc-server")
        gitlab_project_id: GitLab numeric ID (e.g. "59760967")
        since: Optional datetime to fetch commits created after this time.
        """
        stats = {"project_id": project_id, "steps": {}}

        base_url = self.config.get("gitlab_base_url", "https://gitlab.com")
        pat = self.config.get("gitlab_pat", "")
        if not pat:
            return {"error": "GITLAB_PAT not configured"}

        headers = {"PRIVATE-TOKEN": pat}

        # ── Step 1: FETCH metadata ──
        log.info("[ingest] Step 1: FETCH metadata for %s", project_id)
        fetch_stats, commit_files = await self._fetch_gitlab_metadata(
            project_id, gitlab_project_id, base_url, headers, ref, since=since
        )
        stats["steps"]["fetch"] = fetch_stats

        # ── Step 2: CODE — fetch source files ──
        log.info("[ingest] Step 2: CODE — fetch source files")
        fetcher = GitLabCodeFetcher(base_url, pat, gitlab_project_id)
        
        if since is None:
            # Full run: fetch all files
            source_files = fetcher.fetch_source_files(ref=ref)
        else:
            # Incremental run: fetch only files from new commits
            changed_files = set()
            for sha in commit_files:
                for file_diff in commit_files[sha]:
                    # Fetch the new path, unless the file was deleted
                    if not file_diff.get("deleted_file"):
                        changed_files.add(file_diff["path"])
            
            if changed_files:
                source_files = fetcher.fetch_specific_files(list(changed_files), ref=ref)
            else:
                source_files = {}

        count = self.store.upsert_source_files(project_id, source_files)
        stats["steps"]["code"] = {"files_fetched": len(source_files), "stored": count}

        # ── Step 3: PARSE — tree-sitter ──
        log.info("[ingest] Step 3: PARSE — tree-sitter symbol extraction")
        stats["steps"]["parse"] = self._parse_source_files(project_id, source_files)

        # ── Step 4: EMBED — generate embeddings ──
        log.info("[ingest] Step 4: EMBED — generate embeddings")
        stats["steps"]["embed"] = self._generate_embeddings(project_id)

        log.info("[ingest] Done: %s", stats)
        return stats

    async def ingest_from_json(self, project_id: str, data_dir: str | Path) -> dict:
        """
        Ingest from existing JSON files (migration path).
        Reads all *.json from data_dir and stores in PostgreSQL.
        """
        data_dir = Path(data_dir)
        stats = {"project_id": project_id, "steps": {}}

        # Project info
        info_path = data_dir / "info.json"
        if info_path.exists():
            info = json.loads(info_path.read_text(encoding="utf-8"))
            self.store.upsert_project(project_id, {
                "name": info.get("name"),
                "full_name": info.get("path_with_namespace"),
                "description": info.get("description"),
                "visibility": info.get("visibility"),
                "default_branch": info.get("default_branch"),
                "web_url": info.get("web_url"),
                "created_at": info.get("created_at"),
                "source": "gitlab",
                "gitlab_id": str(info.get("id", "")),
            })
            stats["steps"]["project"] = "loaded"

        # Commits
        commits_path = data_dir / "commits.json"
        if commits_path.exists():
            commits = json.loads(commits_path.read_text(encoding="utf-8"))
            count = self.store.upsert_commits(project_id, commits)
            stats["steps"]["commits"] = {"total": len(commits), "stored": count}

        # Members
        members_path = data_dir / "members.json"
        if members_path.exists():
            members = json.loads(members_path.read_text(encoding="utf-8"))
            count = self.store.upsert_members(project_id, members)
            stats["steps"]["members"] = {"total": len(members), "stored": count}

        # Commit files
        files_path = data_dir / "commit_files.json"
        if files_path.exists():
            commit_files = json.loads(files_path.read_text(encoding="utf-8"))
            count = self.store.upsert_commit_files(project_id, commit_files)
            stats["steps"]["commit_files"] = {"total_commits": len(commit_files), "stored": count}

        # Source code
        code_path = data_dir / "source_code.json"
        if code_path.exists():
            code_data = json.loads(code_path.read_text(encoding="utf-8"))
            files = code_data.get("files", {})
            count = self.store.upsert_source_files(project_id, files)
            stats["steps"]["source_files"] = {"total": len(files), "stored": count}

        # Parsed symbols (if already parsed)
        sym_path = data_dir / "parsed_symbols.json"
        if sym_path.exists():
            sym_data = json.loads(sym_path.read_text(encoding="utf-8"))
            symbols = sym_data.get("symbols", [])
            count = self.store.upsert_symbols(project_id, symbols)
            stats["steps"]["symbols"] = {"total": len(symbols), "stored": count}
        else:
            # Parse from source_code.json
            if code_path.exists():
                code_data = json.loads(code_path.read_text(encoding="utf-8"))
                files = code_data.get("files", {})
                stats["steps"]["parse"] = self._parse_source_files(project_id, files)

        # Generate embeddings
        stats["steps"]["embed"] = self._generate_embeddings(project_id)

        log.info("[ingest] JSON migration done: %s", stats)
        return stats

    async def _fetch_gitlab_metadata(
        self, project_id: str, gitlab_project_id: str,
        base_url: str, headers: dict, ref: str,
        since: Optional[datetime] = None,
    ) -> dict:
        """Fetch commits, members, commit_files from GitLab API."""
        stats = {}

        # Project info is not fetched incrementally, it's always updated
        resp = requests.get(
            f"{base_url}/api/v4/projects/{gitlab_project_id}",
            headers=headers, timeout=10,
        )
        if resp.status_code == 200:
            info = resp.json()
            self.store.upsert_project(project_id, {
                "name": info.get("name"),
                "full_name": info.get("path_with_namespace"),
                "description": info.get("description"),
                "visibility": info.get("visibility"),
                "default_branch": info.get("default_branch"),
                "web_url": info.get("web_url"),
                "created_at": info.get("created_at"),
                "source": "gitlab",
                "gitlab_id": gitlab_project_id,
            })
            stats["project"] = "ok"

        # Members are not fetched incrementally
        resp = requests.get(
            f"{base_url}/api/v4/projects/{gitlab_project_id}/members/all",
            headers=headers, params={"per_page": 100}, timeout=10,
        )
        if resp.status_code == 200:
            members = resp.json()
            self.store.upsert_members(project_id, members)
            stats["members"] = len(members)

        # Commits (paginated)
        all_commits = []
        page = 1
        while True:
            params = {
                "ref_name": ref,
                "per_page": 100,
                "page": page,
                "with_stats": "true",
            }
            if since:
                params["since"] = since.isoformat()

            resp = requests.get(
                f"{base_url}/api/v4/projects/{gitlab_project_id}/repository/commits",
                headers=headers,
                params=params,
                timeout=15,
            )
            if resp.status_code != 200 or not resp.json():
                break
            batch = resp.json()
            all_commits.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(0.5)

        self.store.upsert_commits(project_id, all_commits)
        stats["commits"] = len(all_commits)

        if not all_commits:
            stats["commit_files"] = 0
            return stats, {}

        # Commit files (file changes per commit — sample latest 200)
        # For incremental runs, this will be the most recent 200 *new* commits
        commit_files = {}
        for commit in all_commits[:200]:
            sha = commit["id"]
            try:
                resp = requests.get(
                    f"{base_url}/api/v4/projects/{gitlab_project_id}/repository/commits/{sha}/diff",
                    headers=headers, timeout=30,
                )
            except requests.exceptions.RequestException as e:
                log.warning("[fetch] diff fetch failed for %s: %s -- skipping", sha[:8], e)
                continue
            if resp.status_code == 200:
                diffs = resp.json()
                commit_files[sha] = [
                    {
                        "path": d.get("new_path", d.get("old_path", "")),
                        "old_path": d.get("old_path", ""),
                        "new_file": d.get("new_file", False),
                        "deleted_file": d.get("deleted_file", False),
                    }
                    for d in diffs
                ]
            if len(commit_files) % 50 == 0:
                time.sleep(1)

        self.store.upsert_commit_files(project_id, commit_files)
        stats["commit_files"] = len(commit_files)

        return stats, commit_files

    def _parse_source_files(self, project_id: str, source_files: dict[str, str]) -> dict:
        """Parse source files with tree-sitter and store symbols."""
        all_symbols = []
        parsed = 0
        errors = 0

        for filepath, content in source_files.items():
            try:
                content_bytes = content.encode("utf-8")
                result = parse_file_content(content_bytes, filepath)
                if not result:
                    continue

                tree, language = result
                symbols = extract_symbols(tree, content_bytes, filepath, language)
                parsed += 1

                for sym in symbols:
                    if sym.kind == "module":
                        continue
                    all_symbols.append({
                        "name": sym.name,
                        "kind": sym.kind,
                        "qualified_name": sym.qualified_name,
                        "file_path": sym.file_path,
                        "language": sym.language,
                        "start_line": sym.start_line,
                        "end_line": sym.end_line,
                        "signature": sym.signature,
                        "body_hash": sym.body_hash,
                        "params": sym.params,
                        "calls": sym.calls,
                        "imports": sym.imports,
                        "decorators": sym.decorators,
                        "complexity": {
                            "cyclomatic": sym.complexity.cyclomatic,
                            "cognitive": sym.complexity.cognitive,
                            "max_nesting": sym.complexity.max_nesting,
                            "lines": sym.complexity.lines,
                            "params": sym.complexity.params,
                        } if sym.complexity else None,
                    })
            except Exception as e:
                errors += 1
                log.warning("[ingest] Parse error %s: %s", filepath, e)

        count = self.store.upsert_symbols(project_id, all_symbols)
        return {
            "files_parsed": parsed,
            "symbols_extracted": len(all_symbols),
            "stored": count,
            "errors": errors,
        }

    def _generate_embeddings(self, project_id: str) -> dict:
        """Generate embeddings for symbols via OpenRouter and store in PostgreSQL."""
        api_key = self.config.get("openrouter_api_key", "")
        if not api_key:
            return {"skipped": "no OpenRouter API key"}

        # Get all symbols that need embedding
        symbols = self.store.get_symbols(project_id)
        if not symbols:
            return {"skipped": "no symbols to embed"}

        # Filter to functions/methods only (classes are less useful for semantic search)
        to_embed = [
            s for s in symbols
            if s["kind"] in ("function", "method")
            and s.get("qualified_name")
            and ".min." not in s.get("file_path", "")
            and "/public/js/" not in s.get("file_path", "")
        ]

        # Check what's already embedded
        existing = self.store.get_embedding_count(project_id)
        if existing >= len(to_embed):
            return {"skipped": f"already embedded ({existing}/{len(to_embed)})"}

        log.info("[ingest] Embedding %d symbols", len(to_embed))

        # Build texts for embedding
        texts = []
        symbol_ids = []
        for sym in to_embed:
            symbol_id = f"{project_id}:{sym['file_path']}:{sym['qualified_name']}"
            text = f"{sym['qualified_name']} {sym.get('signature', '')} {sym.get('file_path', '')}"
            # Add params and calls for richer context
            if sym.get("params"):
                text += f" params: {' '.join(sym['params'])}"
            if sym.get("calls"):
                text += f" calls: {' '.join(sym['calls'][:10])}"
            texts.append(text)
            symbol_ids.append(symbol_id)

        # Batch embed via OpenRouter
        all_embeddings = []
        batch_size = 50
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_ids = symbol_ids[i:i + batch_size]
            batch_syms = to_embed[i:i + batch_size]

            try:
                resp = requests.post(
                    self.config.get("openrouter_embed_url", "https://openrouter.ai/api/v1/embeddings"),
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.config.get("openrouter_embed_model", "openai/text-embedding-3-small"),
                        "input": batch_texts,
                    },
                    timeout=30,
                )
                result = resp.json()

                if "data" in result:
                    for j, item in enumerate(result["data"]):
                        all_embeddings.append({
                            "symbol_id": batch_ids[j],
                            "qualified_name": batch_syms[j]["qualified_name"],
                            "file_path": batch_syms[j]["file_path"],
                            "kind": batch_syms[j]["kind"],
                            "text": batch_texts[j][:500],
                            "vector": item["embedding"],
                        })
                else:
                    log.error("[ingest] Embedding API error: %s", result)

            except Exception as e:
                log.error("[ingest] Embedding batch failed: %s", e)

            if i + batch_size < len(texts):
                time.sleep(1.5)  # Rate limiting

            log.info("[ingest] Embedded %d/%d", min(i + batch_size, len(texts)), len(texts))

        count = self.store.upsert_embeddings(project_id, all_embeddings)
        return {
            "symbols_embedded": len(all_embeddings),
            "stored": count,
            "total_symbols": len(to_embed),
        }

    # ── GitHub ingestion ─────────────────────────────────────────────

    async def ingest_github_project(
        self,
        owner: str,
        repo: str,
        ref: str = "main",
        since: Optional[datetime] = None,
    ) -> dict:
        """
        Full ingestion for a GitHub repository.
        owner/repo: e.g. "isitraghav/stelmar"
        Uses GITHUB_PAT from config.
        """
        project_id = f"{owner}/{repo}"
        stats: dict = {"project_id": project_id, "steps": {}}

        pat = self.config.get("github_pat", "")
        if not pat:
            return {"error": "GITHUB_PAT not configured"}

        headers = {
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github.v3+json",
        }
        base = "https://api.github.com"

        # ── Step 1: FETCH metadata ──
        log.info("[ingest-gh] Step 1: FETCH metadata for %s", project_id)
        fetch_stats, commit_files = await self._fetch_github_metadata(
            owner, repo, headers, base, since=since
        )
        stats["steps"]["fetch"] = fetch_stats

        # ── Step 2: CODE — source files ──
        log.info("[ingest-gh] Step 2: CODE — fetch source files")
        fetcher = GitHubCodeFetcher(pat, owner, repo)
        if since is None:
            source_files = fetcher.fetch_source_files(ref=ref)
        else:
            changed = {
                f.get("filename", "")
                for files in commit_files.values()
                for f in files
                if not f.get("deleted_file")
            }
            source_files = fetcher.fetch_specific_files(list(changed), ref=ref) if changed else {}

        count = self.store.upsert_source_files(project_id, source_files)
        stats["steps"]["code"] = {"files_fetched": len(source_files), "stored": count}

        # ── Step 3: PARSE ──
        log.info("[ingest-gh] Step 3: PARSE")
        stats["steps"]["parse"] = self._parse_source_files(project_id, source_files)

        # ── Step 4: EMBED ──
        log.info("[ingest-gh] Step 4: EMBED")
        stats["steps"]["embed"] = self._generate_embeddings(project_id)

        log.info("[ingest-gh] Done: %s", stats)
        return stats

    async def _fetch_github_metadata(
        self,
        owner: str,
        repo: str,
        headers: dict,
        base: str,
        since: Optional[datetime] = None,
    ) -> tuple[dict, dict]:
        """Fetch project info, commits, contributors, commit files from GitHub."""
        project_id = f"{owner}/{repo}"
        stats: dict = {}

        # Project info
        resp = requests.get(f"{base}/repos/{owner}/{repo}", headers=headers, timeout=10)
        if resp.status_code == 200:
            info = resp.json()
            self.store.upsert_project(project_id, {
                "name": info.get("name"),
                "full_name": info.get("full_name"),
                "description": info.get("description"),
                "visibility": "private" if info.get("private") else "public",
                "default_branch": info.get("default_branch", "main"),
                "web_url": info.get("html_url"),
                "created_at": info.get("created_at"),
                "source": "github",
                "github_id": str(info.get("id", "")),
            })
            stats["project"] = "ok"

        # Contributors (→ members)
        resp = requests.get(
            f"{base}/repos/{owner}/{repo}/contributors",
            headers=headers, params={"per_page": 100}, timeout=10,
        )
        if resp.status_code == 200:
            members = [
                {
                    "username": m["login"],
                    "name": m["login"],
                    "web_url": m.get("html_url", ""),
                    "avatar_url": m.get("avatar_url", ""),
                    "contributions": m.get("contributions", 0),
                }
                for m in resp.json()
                if isinstance(m, dict)
            ]
            self.store.upsert_members(project_id, members)
            stats["members"] = len(members)

        # Commits (paginated, up to 500)
        all_commits = []
        page = 1
        params: dict = {"per_page": 100, "page": page}
        if since:
            params["since"] = since.isoformat()

        while len(all_commits) < 500:
            params["page"] = page
            resp = requests.get(
                f"{base}/repos/{owner}/{repo}/commits",
                headers=headers, params=params, timeout=15,
            )
            if resp.status_code != 200 or not resp.json():
                break
            batch = resp.json()
            all_commits.extend(batch)
            if len(batch) < 100:
                break
            page += 1
            time.sleep(0.3)

        # Normalise GitHub commit shape → pipeline shape
        normalised = []
        for c in all_commits:
            commit_data = c.get("commit", {})
            author = commit_data.get("author") or {}
            stats_data = c.get("stats") or {}
            normalised.append({
                "id": c["sha"],
                "sha": c["sha"],
                "short_id": c["sha"][:8],
                "title": (commit_data.get("message", "") or "").split("\n")[0][:200],
                "message": commit_data.get("message", ""),
                "author_name": author.get("name", ""),
                "author_email": author.get("email", ""),
                "authored_date": author.get("date", ""),
                "committed_date": (commit_data.get("committer") or {}).get("date", ""),
                "parent_ids": [p["sha"] for p in c.get("parents", [])],
                "stats": {
                    "additions": stats_data.get("additions", 0),
                    "deletions": stats_data.get("deletions", 0),
                },
                "web_url": c.get("html_url", ""),
            })
        self.store.upsert_commits(project_id, normalised)
        stats["commits"] = len(normalised)

        if not normalised:
            return stats, {}

        # Commit files (latest 100 commits only to stay within rate limits)
        commit_files: dict[str, list[dict]] = {}
        for commit in normalised[:100]:
            sha = commit["sha"]
            try:
                resp = requests.get(
                    f"{base}/repos/{owner}/{repo}/commits/{sha}",
                    headers=headers, timeout=15,
                )
            except requests.exceptions.RequestException as e:
                log.warning("[fetch-gh] diff fetch failed for %s: %s", sha[:8], e)
                continue
            if resp.status_code == 200:
                data = resp.json()
                commit_files[sha] = [
                    {
                        "path": f.get("filename", ""),
                        "old_path": f.get("previous_filename", ""),
                        "new_file": f.get("status") == "added",
                        "deleted_file": f.get("status") == "removed",
                        "renamed_file": f.get("status") == "renamed",
                    }
                    for f in data.get("files", [])
                ]
            if len(commit_files) % 20 == 0 and commit_files:
                time.sleep(1)

        self.store.upsert_commit_files(project_id, commit_files)
        stats["commit_files"] = len(commit_files)
        return stats, commit_files
