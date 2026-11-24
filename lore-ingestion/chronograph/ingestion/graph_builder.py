"""
Graph builder — extracts relationships from stored data and creates edges.

Builds the temporal knowledge graph structure:
  - Snapshots: commit chain with timestamps
  - AUTHORED_BY: developer → code entity (from commit_files + symbols)
  - CALLS: function → function (from symbol.calls)
  - CONTAINS: class → method (from qualified_name hierarchy)
  - IMPORTS: file → module (from symbol.imports)
  - COLLABORATES_WITH: developer ↔ developer (from co-changed files)

Works against the ChronographStore protocol — no direct store access.
"""

import logging

from chronograph.diffing.change_classifier import classify_commit

log = logging.getLogger("chronograph.graph_builder")


class GraphBuilder:
    """Build graph relationships from stored data using the abstract store interface."""

    def __init__(self, store):
        self.store = store

    def build_all(self, project_id: str) -> dict:
        stats = {}
        stats["snapshots"] = self._build_snapshots(project_id)
        stats["authored_by"] = self._build_authored_by(project_id)
        stats["calls"] = self._build_calls(project_id)
        stats["contains"] = self._build_contains(project_id)
        stats["imports"] = self._build_imports(project_id)
        stats["collaborates_with"] = self._build_collaborates_with(project_id)
        log.info("[graph_builder] Built all relationships: %s", stats)
        return stats

    def _build_snapshots(self, project_id: str) -> int:
        commits = self.store.get_commits(project_id)
        commits.sort(key=lambda c: c.get("authored_date", ""))

        snapshots = []
        edges = []
        prev_sha = None

        for c in commits:
            classification = classify_commit(c.get("message", ""))
            parent_ids = c.get("parent_ids", [])
            snapshots.append({
                "commit_sha": c["sha"],
                "timestamp": c.get("authored_date", ""),
                "author_email": c.get("author_email", ""),
                "author_name": c.get("author_name", ""),
                "message": c.get("message", "")[:500],
                "classification": classification,
                "parent_sha": parent_ids[0] if parent_ids else None,
            })
            if prev_sha:
                edges.append({
                    "source_id": f"snap:{prev_sha[:12]}",
                    "target_id": f"snap:{c['sha'][:12]}",
                    "rel_type": "NEXT_SNAPSHOT",
                    "valid_from": c.get("authored_date", ""),
                })
            prev_sha = c["sha"]

        self.store.upsert_snapshots_bulk(project_id, snapshots)
        self.store.create_edges_bulk(project_id, edges)
        log.info("[graph_builder] %d snapshots, %d NEXT_SNAPSHOT edges", len(snapshots), len(edges))
        return len(snapshots)

    def _build_authored_by(self, project_id: str) -> int:
        commit_file_authors = self.store.get_commit_file_authors(project_id)

        file_authors: dict[str, dict[str, str]] = {}
        for row in commit_file_authors:
            path = row["file_path"]
            email = row["author_email"]
            date = row.get("authored_date", "")
            if path not in file_authors:
                file_authors[path] = {}
            if email not in file_authors[path] or date < file_authors[path][email]:
                file_authors[path][email] = date

        symbols = self.store.get_symbols(project_id)
        file_symbols: dict[str, list] = {}
        for s in symbols:
            file_symbols.setdefault(s["file_path"], []).append(s)

        edges = []
        for path, authors in file_authors.items():
            for sym in file_symbols.get(path, []):
                entity_id = f"entity:{project_id}:{sym['file_path']}:{sym['qualified_name']}"
                for email, earliest_date in authors.items():
                    edges.append({
                        "source_id": entity_id,
                        "target_id": f"dev:{email}",
                        "rel_type": "AUTHORED_BY",
                        "valid_from": earliest_date,
                        "properties": {"file_path": path},
                    })

        self.store.create_edges_bulk(project_id, edges)
        log.info("[graph_builder] %d AUTHORED_BY edges", len(edges))
        return len(edges)

    def _build_calls(self, project_id: str) -> int:
        symbols = self.store.get_symbols(project_id)
        func_syms = [s for s in symbols if s.get("kind") in ("function", "method")]

        by_name: dict[str, list] = {}
        for s in func_syms:
            by_name.setdefault(s["name"], []).append(s)

        edges = []
        for sym in func_syms:
            if not sym.get("calls"):
                continue
            source_id = f"entity:{project_id}:{sym['file_path']}:{sym['qualified_name']}"
            for call_name in sym["calls"]:
                clean = call_name.split("(")[0].strip()
                if clean.startswith("this."):
                    clean = clean[5:]
                if clean.startswith("self."):
                    clean = clean[5:]
                targets = by_name.get(clean, [])
                if targets:
                    for t in targets:
                        target_id = f"entity:{project_id}:{t['file_path']}:{t['qualified_name']}"
                        if source_id != target_id:
                            edges.append({
                                "source_id": source_id,
                                "target_id": target_id,
                                "rel_type": "CALLS",
                                "properties": {"raw_call": call_name[:100]},
                            })
                else:
                    edges.append({
                        "source_id": source_id,
                        "target_id": f"external:{clean}",
                        "rel_type": "CALLS",
                        "properties": {"raw_call": call_name[:100], "resolved": False},
                    })

        seen: set = set()
        unique = []
        for e in edges:
            key = (e["source_id"], e["target_id"], e["rel_type"])
            if key not in seen:
                seen.add(key)
                unique.append(e)

        self.store.create_edges_bulk(project_id, unique)
        log.info("[graph_builder] %d CALLS edges (%d before dedup)", len(unique), len(edges))
        return len(unique)

    def _build_contains(self, project_id: str) -> int:
        symbols = self.store.get_symbols(project_id)
        by_file: dict[str, list] = {}
        for s in symbols:
            by_file.setdefault(s["file_path"], []).append(s)

        edges = []
        for path, syms in by_file.items():
            classes = {s["qualified_name"]: s for s in syms if s["kind"] == "class"}
            for s in syms:
                if s["kind"] in ("method", "function"):
                    parts = s["qualified_name"].rsplit(".", 1)
                    if len(parts) == 2 and parts[0] in classes:
                        edges.append({
                            "source_id": f"entity:{project_id}:{path}:{parts[0]}",
                            "target_id": f"entity:{project_id}:{path}:{s['qualified_name']}",
                            "rel_type": "CONTAINS",
                        })

        self.store.create_edges_bulk(project_id, edges)
        log.info("[graph_builder] %d CONTAINS edges", len(edges))
        return len(edges)

    def _build_imports(self, project_id: str) -> int:
        symbols = self.store.get_symbols(project_id)
        edges = []
        seen: set = set()
        for sym in symbols:
            imports = sym.get("imports") or []
            if not imports:
                continue
            file_id = f"file:{project_id}:{sym['file_path']}"
            for imp in imports:
                target_id = f"module:{imp}"
                key = (file_id, target_id)
                if key not in seen:
                    seen.add(key)
                    edges.append({
                        "source_id": file_id,
                        "target_id": target_id,
                        "rel_type": "IMPORTS",
                        "properties": {"module_name": imp},
                    })

        self.store.create_edges_bulk(project_id, edges)
        log.info("[graph_builder] %d IMPORTS edges", len(edges))
        return len(edges)

    def _build_collaborates_with(self, project_id: str) -> int:
        shared_files = self.store.get_file_authors_by_path(project_id)

        pair_files: dict[tuple, list] = {}
        for row in shared_files:
            authors = sorted(row.get("authors", []))
            path = row["file_path"]
            for i in range(len(authors)):
                for j in range(i + 1, len(authors)):
                    pair = (authors[i], authors[j])
                    pair_files.setdefault(pair, []).append(path)

        edges = []
        for (a, b), files in pair_files.items():
            edges.append({
                "source_id": f"dev:{a}",
                "target_id": f"dev:{b}",
                "rel_type": "COLLABORATES_WITH",
                "properties": {"shared_files": len(files), "files": files[:20]},
            })

        self.store.create_edges_bulk(project_id, edges)
        log.info("[graph_builder] %d COLLABORATES_WITH edges", len(edges))
        return len(edges)
