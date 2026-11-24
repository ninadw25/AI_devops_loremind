"""
chronograph/poller.py
──────────────────────
Source-agnostic poll cycle. Accepts any plugin (GitLabPlugin, GitHubPlugin, …)
that implements `fetch_commits`, `fetch_commit_files`, `fetch_source_files`,
`fetch_members`, and `fetch_project_info`.

One cycle = one source × one project:
  1. Load checkpoint
  2. Fetch commits (since checkpoint)
  3. Fetch file diffs for new commits
  4. Fetch changed source files
  5. Fetch members (always refreshed)
  6. Run parse → embed → graph-build pipeline
  7. Save checkpoint on success

Checkpoint is NOT advanced on any exception, so the next cycle retries.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Union

from chronograph import polling_checkpoint as cp
from chronograph.store.base import ChronographStore

log = logging.getLogger("chronograph.poller")

MAX_DIFF_COMMITS = 200  # cap per cycle to bound API calls


async def run_cycle(
    plugin: Any,
    store: ChronographStore,
    ref: str = "",
) -> dict:
    """
    Run one poll cycle for the given plugin + store.

    Returns a stats dict (always — never raises, so the scheduler loop
    keeps running even if one cycle fails).
    """
    source = plugin.name
    repo = plugin.repo
    project_id = repo.full_name
    cycle_start = datetime.now(timezone.utc)

    log.info("[poller] Start cycle — source=%s project=%s", source, project_id)
    stats: dict = {"source": source, "project": project_id, "steps": {}}

    try:
        since = cp.load(source, project_id)
        log.info("[poller] Checkpoint: %s", since.isoformat() if since else "FIRST RUN")

        # ── Step 1: Project metadata ───────────────────────────────────
        project_info = plugin.fetch_project_info()
        store.upsert_project(project_id, project_info)
        if not ref:
            ref = project_info.get("default_branch") or "main"
        stats["steps"]["project"] = "ok"

        # ── Step 2: Members ────────────────────────────────────────────
        raw_members = plugin.fetch_members()
        store.upsert_members(project_id, [
            {
                "username": m.login,
                "name": m.name,
                "email": m.email,
                "role": m.role,
                "avatar_url": m.avatar_url,
                "web_url": m.web_url,
                "project_id": project_id,
            }
            for m in raw_members
        ])
        stats["steps"]["members"] = len(raw_members)

        # ── Step 3: Commits ────────────────────────────────────────────
        commits = plugin.fetch_commits(since=since, ref=ref)
        if not commits:
            log.info("[poller] No new commits for %s/%s", source, project_id)
            # Still save checkpoint so we don't re-poll an already-up-to-date repo
            cp.save(source, project_id, cycle_start)
            stats["steps"]["commits"] = 0
            return stats

        store.upsert_commits(project_id, [
            {
                "id": c.sha,
                "short_id": c.short_id,
                "title": c.title,
                "message": c.message,
                "author_name": c.author_name,
                "author_email": c.author_email,
                "authored_date": c.authored_date.isoformat() if c.authored_date else "",
                "committed_date": c.committed_date.isoformat() if c.committed_date else None,
                "parent_ids": c.parent_shas,
                "stats": c.stats,
                "web_url": c.web_url,
            }
            for c in commits
        ])
        stats["steps"]["commits"] = len(commits)
        log.info("[poller] %d new commits", len(commits))

        # ── Step 4: Diffs ──────────────────────────────────────────────
        commit_files: dict[str, list[dict]] = {}
        changed_paths: set[str] = set()
        fetch_limit = min(len(commits), MAX_DIFF_COMMITS)

        for commit in commits[:fetch_limit]:
            diffs = plugin.fetch_commit_files(commit.sha)
            if diffs:
                commit_files[commit.sha] = [
                    {
                        "path": d.path,
                        "old_path": d.old_path,
                        "new_file": d.new_file,
                        "deleted_file": d.deleted_file,
                        "additions": d.additions,
                        "deletions": d.deletions,
                    }
                    for d in diffs
                ]
                for d in diffs:
                    if not d.deleted_file and d.path:
                        changed_paths.add(d.path)

        store.upsert_commit_files(project_id, commit_files)
        stats["steps"]["commit_files"] = len(commit_files)

        if fetch_limit < len(commits):
            log.warning(
                "[poller] Only fetched diffs for first %d of %d commits (cycle cap).",
                fetch_limit, len(commits),
            )

        # ── Step 5: Source files ───────────────────────────────────────
        if since is None:
            # First run: fetch all source files
            source_files = plugin.fetch_source_files(ref=ref, paths=None)
        elif changed_paths:
            source_files = plugin.fetch_source_files(ref=ref, paths=list(changed_paths))
        else:
            source_files = {}

        store.upsert_source_files(project_id, source_files)
        stats["steps"]["source_files"] = len(source_files)

        # ── Step 5.5: PRs + Issues (GitHub only, best-effort) ─────────
        if hasattr(plugin, "fetch_pull_requests"):
            try:
                prs = plugin.fetch_pull_requests()
                store.upsert_pull_requests(project_id, prs)
                stats["steps"]["pull_requests"] = len(prs)

                issues = plugin.fetch_issues()
                store.upsert_issues(project_id, issues)
                stats["steps"]["issues"] = len(issues)
                log.info("[poller] %d PRs, %d issues fetched", len(prs), len(issues))
            except Exception as exc:
                log.warning("[poller] PR/issue fetch failed (non-fatal): %s", exc)

        # ── Step 6: Parse + embed ──────────────────────────────────────
        if source_files:
            parse_stats = _parse_and_store(store, project_id, source_files)
            stats["steps"]["parse"] = parse_stats

            embed_stats = _embed_symbols(store, project_id)
            stats["steps"]["embed"] = embed_stats

        # ── Step 7: Graph build ────────────────────────────────────────
        if source_files or commits:
            graph_stats = _build_graph(store, project_id)
            stats["steps"]["graph"] = graph_stats

        # ── Checkpoint (success only) ──────────────────────────────────
        cp.save(source, project_id, cycle_start)
        log.info("[poller] Cycle complete: %s", stats)
        return stats

    except Exception as exc:
        log.error(
            "[poller] Cycle FAILED for %s/%s: %s — checkpoint NOT advanced",
            source, project_id, exc,
            exc_info=True,
        )
        stats["error"] = str(exc)
        return stats


# ── Pipeline stages (shared across sources) ────────────────────────────


def _parse_and_store(store: ChronographStore, project_id: str, source_files: dict[str, str]) -> dict:
    from chronograph.parsing.parser import parse_file_content
    from chronograph.parsing.symbol_extractor import extract_symbols

    all_symbols = []
    parsed = errors = 0
    for filepath, content in source_files.items():
        try:
            result = parse_file_content(content.encode("utf-8"), filepath)
            if not result:
                continue
            tree, language = result
            symbols = extract_symbols(tree, content.encode("utf-8"), filepath, language)
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
        except Exception as exc:
            errors += 1
            log.warning("[poller] Parse error %s: %s", filepath, exc)

    count = store.upsert_symbols(project_id, all_symbols)
    return {"files_parsed": parsed, "symbols": len(all_symbols), "stored": count, "errors": errors}


def _embed_symbols(store: ChronographStore, project_id: str) -> dict:
    """Embed symbols using NVIDIA NeMo (llama-nemotron-embed-vl-1b-v2, 2048-d)."""
    from common.config import get_settings
    from openai import OpenAI
    import time as _time

    settings = get_settings()
    api_key = settings.nvidia_api_key
    if not api_key:
        return {"skipped": "NVIDIA_API_KEY not set"}

    model = settings.embedding_model          # nvidia/llama-nemotron-embed-vl-1b-v2
    base_url = settings.nvidia_base_url       # https://integrate.api.nvidia.com/v1

    symbols = store.get_symbols(project_id)
    to_embed = [
        s for s in symbols
        if s["kind"] in ("function", "method")
        and s.get("qualified_name")
        and ".min." not in s.get("file_path", "")
        and "/public/js/" not in s.get("file_path", "")
    ]
    existing = store.get_embedding_count(project_id)
    if existing >= len(to_embed):
        return {"skipped": f"already embedded ({existing}/{len(to_embed)})"}

    client = OpenAI(api_key=api_key, base_url=base_url)

    texts, ids, syms = [], [], []
    for sym in to_embed:
        sid = f"{project_id}:{sym['file_path']}:{sym['qualified_name']}"
        text = f"{sym['qualified_name']} {sym.get('signature', '')} {sym.get('file_path', '')}"
        if sym.get("params"):
            text += f" params: {' '.join(sym['params'])}"
        if sym.get("calls"):
            text += f" calls: {' '.join(sym['calls'][:10])}"
        texts.append(text)
        ids.append(sid)
        syms.append(sym)

    all_embeddings = []
    batch_size = min(settings.embed_batch_size, 50)  # NVIDIA free tier cap
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        try:
            resp = client.embeddings.create(
                input=batch_texts,
                model=model,
                encoding_format="float",
                extra_body={"input_type": "passage", "truncate": "END"},
            )
            for j, item in enumerate(resp.data):
                all_embeddings.append({
                    "symbol_id": ids[i + j],
                    "qualified_name": syms[i + j]["qualified_name"],
                    "file_path": syms[i + j]["file_path"],
                    "kind": syms[i + j]["kind"],
                    "text": batch_texts[j][:500],
                    "vector": item.embedding,
                })
            log.info("[poller] Embedded batch %d/%d", min(i + batch_size, len(texts)), len(texts))
        except Exception as exc:
            log.error("[poller] Embedding batch %d failed: %s", i, exc)
        if i + batch_size < len(texts):
            _time.sleep(settings.embed_delay_s)

    count = store.upsert_embeddings(project_id, all_embeddings)
    return {"embedded": len(all_embeddings), "stored": count, "total": len(to_embed), "model": model}


def _build_graph(store: ChronographStore, project_id: str) -> dict:
    from chronograph.ingestion.graph_builder import GraphBuilder
    stats = GraphBuilder(store).build_all(project_id)
    # Build per-file metadata after graph edges are in place
    try:
        from chronograph.store.pg_store import PgStore
        if isinstance(store, PgStore):
            n = store.build_file_metadata(project_id)
            stats["file_metadata"] = n
    except Exception as exc:
        log.warning("[poller] file_metadata build failed: %s", exc)
    return stats
