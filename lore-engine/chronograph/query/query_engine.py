"""
Query engine — answers questions using store data + embeddings + LLM.

Three query modes:
  1. Structured — direct store lookups (hotspots, bus factor, complexity, etc.)
  2. Semantic — embed query → pgvector ANN
  3. Chat — build context from structured + semantic, send to LLM for synthesis
"""

import logging
import requests
from typing import Optional

log = logging.getLogger("chronograph.query_engine")


class QueryEngine:
    """Store-backed query engine with semantic search and LLM chat."""

    def __init__(self, store, config: dict):
        self.store = store
        self.config = config

    # ── Structured Queries ──

    def get_stats(self, project_id: str) -> dict:
        return self.store.get_stats(project_id)

    def get_people(self, project_id: str) -> list[dict]:
        return self.store.get_commit_authors(project_id)

    def get_hotspots(self, project_id: str, limit: int = 20) -> list[dict]:
        return self.store.compute_hotspots(project_id, limit=limit)

    def get_bus_factor(self, project_id: str, min_changes: int = 5) -> list[dict]:
        return self.store.compute_bus_factor(project_id, min_changes=min_changes)

    def get_timeline(self, project_id: str, bucket: str = "month") -> list[dict]:
        return self.store.get_activity_timeline(project_id, bucket=bucket)

    def get_complexity(self, project_id: str, limit: int = 20) -> list[dict]:
        return self.store.get_most_complex(
            project_id, limit=limit,
            exclude_patterns=[r"\.min\.", r"/public/js/"],
        )

    def search_symbols(self, project_id: str, query: str,
                       kind: Optional[str] = None, limit: int = 20) -> list[dict]:
        return self.store.search_symbols(project_id, query, kind=kind, limit=limit)

    def trace_function(self, project_id: str, function_name: str) -> dict:
        return self.store.trace_function(project_id, function_name)

    def get_function_code(self, project_id: str, qualified_name: str) -> Optional[dict]:
        sym = self.store.get_symbol(project_id, qualified_name)
        if not sym:
            return None
        content = self.store.get_source_file(project_id, sym["file_path"])
        if not content:
            return {**sym, "source": None}
        lines = content.split("\n")
        start = max(0, sym["start_line"] - 1)
        end = min(len(lines), sym["end_line"])
        return {**sym, "source": "\n".join(lines[start:end])}

    def get_file_content(self, project_id: str, file_path: str) -> Optional[dict]:
        content = self.store.get_source_file(project_id, file_path)
        if content is None:
            return None
        symbols = self.store.get_symbols(project_id, file_path=file_path)
        return {"file_path": file_path, "content": content, "symbols": symbols}

    # ── Semantic Search ──

    def semantic_search(self, project_id: str, query: str, top_k: int = 10) -> list[dict]:
        """Embed query via NVIDIA NeMo, find most similar symbols via pgvector <=>."""
        query_vector = self._embed_query(query)
        if not query_vector:
            return []
        return self.store.search_embeddings(project_id, query_vector, top_k=top_k)

    def _embed_query(self, text: str) -> Optional[list[float]]:
        """Embed a search query using NVIDIA NeMo with input_type=query."""
        from openai import OpenAI
        api_key = self.config.get("nvidia_api_key", "")
        if not api_key:
            log.warning("[query] NVIDIA_API_KEY not set — semantic search unavailable")
            return None
        model = self.config.get("embedding_model", "nvidia/llama-nemotron-embed-vl-1b-v2")
        base_url = self.config.get("nvidia_base_url", "https://integrate.api.nvidia.com/v1")
        try:
            client = OpenAI(api_key=api_key, base_url=base_url)
            resp = client.embeddings.create(
                input=[text],
                model=model,
                encoding_format="float",
                extra_body={"input_type": "query", "truncate": "END"},
            )
            return resp.data[0].embedding
        except Exception as exc:
            log.error("[query] NVIDIA embed failed: %s", exc)
        return None

    # ── Chat (LLM-powered) ──

    def _llm_chat(self, system_prompt: str, user_content: str,
                  max_tokens: int = 800, temperature: float = 0.3) -> dict:
        """Provider-aware one-shot chat. Returns {'answer': ...} or {'error': ...}.

        Uses AWS Bedrock (Converse API) when config llm_provider == 'bedrock'
        (auth via the AWS_BEARER_TOKEN_BEDROCK bearer token, mirroring the Gmail
        query pipeline in lore-engine); otherwise falls back to OpenRouter.
        """
        provider = (self.config.get("llm_provider") or "").lower()
        if provider == "bedrock":   # no bedrock_api_key: boto3's default chain (instance role, AWS_PROFILE)
            import os
            import boto3
            if self.config.get("bedrock_api_key"):
                os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", self.config["bedrock_api_key"])
            try:
                client = boto3.client(
                    "bedrock-runtime",
                    region_name=self.config.get("bedrock_region", "ap-south-1"),
                )
                resp = client.converse(
                    modelId=self.config.get(
                        "bedrock_model", "apac.anthropic.claude-3-5-sonnet-20241022-v2:0"
                    ),
                    system=[{"text": system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_content}]}],
                    inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
                )
                parts = resp["output"]["message"]["content"]
                return {"answer": "".join(p.get("text", "") for p in parts)}
            except Exception as e:
                return {"error": f"Bedrock chat failed: {e}"}

        api_key = self.config.get("openrouter_api_key", "")
        if not api_key:
            return {"error": "No LLM configured (set llm_provider=bedrock + "
                             "bedrock_api_key, or openrouter_api_key)"}
        try:
            resp = requests.post(
                self.config.get("openrouter_chat_url",
                                "https://openrouter.ai/api/v1/chat/completions"),
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": self.config.get("openrouter_llm_model",
                                             "google/gemini-2.0-flash-001"),
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=30,
            )
            result = resp.json()
            if "choices" in result:
                return {"answer": result["choices"][0]["message"]["content"],
                        "usage": result.get("usage", {})}
            return {"error": f"LLM error: {result}"}
        except Exception as e:
            return {"error": f"Chat failed: {e}"}

    def chat(self, project_id: str, question: str) -> dict:
        """Answer a natural language question using structured data + semantic search + LLM."""
        context_parts = []

        project = self.store.get_project(project_id)
        if project:
            context_parts.append(
                f"PROJECT: {project.get('full_name', project_id)} ({project.get('visibility', '')})"
            )

        authors = self.store.get_commit_authors(project_id)
        if authors:
            lines = [
                f"- {a['name']} ({a['email']}): {a['commit_count']} commits, "
                f"+{a.get('additions',0)}/-{a.get('deletions',0)}"
                for a in authors[:10]
            ]
            context_parts.append("DEVELOPERS:\n" + "\n".join(lines))

        relevant_code = self.semantic_search(project_id, question, top_k=5)
        if relevant_code:
            code_lines = []
            for r in relevant_code:
                sym = self.store.get_symbol(project_id, r["qualified_name"])
                if sym:
                    cx = sym.get("complexity") or {}
                    code_lines.append(
                        f"- {sym['kind']} {sym['qualified_name']} in "
                        f"{sym['file_path']}:{sym['start_line']} "
                        f"(complexity={cx.get('cyclomatic','?')}, "
                        f"calls={sym.get('calls', [])[:5]})"
                    )
            if code_lines:
                context_parts.append("RELEVANT CODE SYMBOLS:\n" + "\n".join(code_lines))

        hotspots = self.store.compute_hotspots(project_id, limit=10)
        if hotspots:
            context_parts.append("TOP HOTSPOT FILES:\n" + "\n".join(
                f"- {h['file_path']}: {h['change_count']} changes" for h in hotspots
            ))

        bus = self.store.compute_bus_factor(project_id, min_changes=3)
        if bus:
            context_parts.append(
                "BUS FACTOR RISKS (files owned by single developer):\n" + "\n".join(
                    f"- {b['file_path']}: {b['total_changes']} changes, "
                    f"sole author: {b['sole_author']}"
                    for b in bus[:15]
                )
            )

        expertise = self.store.get_developer_file_expertise(project_id, limit=30)
        if expertise:
            by_author: dict[str, list] = {}
            for e in expertise:
                by_author.setdefault(e["author"], []).append(
                    f"{e['path']} ({e['count']} changes)"
                )
            lines = [
                f"- {author}: {', '.join(files[:8])}"
                for author, files in by_author.items()
            ]
            context_parts.append("DEVELOPER FILE EXPERTISE:\n" + "\n".join(lines))

        complex_funcs = self.store.get_most_complex(
            project_id, limit=5,
            exclude_patterns=[r"\.min\.", r"/public/js/"],
        )
        if complex_funcs:
            context_parts.append("MOST COMPLEX FUNCTIONS:\n" + "\n".join(
                f"- {s['qualified_name']} ({s['file_path']}) — "
                f"cyclomatic={s['complexity']['cyclomatic']}, "
                f"lines={s['complexity']['lines']}"
                for s in complex_funcs
            ))

        if relevant_code:
            func = self.get_function_code(project_id, relevant_code[0]["qualified_name"])
            if func and func.get("source"):
                context_parts.append(
                    f"SOURCE CODE ({relevant_code[0]['qualified_name']}):\n"
                    f"```\n{func['source'][:500]}\n```"
                )

        context = "\n\n".join(context_parts)

        system_prompt = (
            "You are an engineering intelligence assistant analyzing a software "
            "project. Use ONLY the provided data. Be specific with file names, "
            "function names, and numbers. Keep answers concise (2-3 paragraphs max)."
        )
        user_content = f"PROJECT DATA:\n{context}\n\nQUESTION: {question}"
        out = self._llm_chat(system_prompt, user_content, max_tokens=800, temperature=0.3)
        if "error" in out:
            return out
        return {
            "answer": out["answer"],
            "source": "llm",
            "context_length": len(context),
            "usage": out.get("usage", {}),
            "relevant_symbols": [r["qualified_name"] for r in relevant_code[:3]],
        }

    def search_commits(self, project_id: str, query: str, limit: int = 20) -> list[dict]:
        """Keyword search in commit messages."""
        return self.store.search_commits_by_message(project_id, query, limit=limit)

    # ── Graph Traversal Queries ──

    def what_does_developer_own(self, project_id: str, email: str) -> dict:
        edges = self.store.get_edges(project_id, target_id=f"dev:{email}", rel_type="AUTHORED_BY")
        files: dict[str, list] = {}
        for e in edges:
            parts = e["source_id"].split(":", 3)
            if len(parts) >= 4:
                files.setdefault(parts[2], []).append(parts[3])
        return {
            "developer": email,
            "files_owned": len(files),
            "symbols_owned": sum(len(v) for v in files.values()),
            "files": {k: v for k, v in sorted(files.items(), key=lambda x: -len(x[1]))},
        }

    def what_breaks_if_changed(self, project_id: str, qualified_name: str) -> dict:
        sym = self.store.get_symbol(project_id, qualified_name)
        if not sym:
            return {"error": f"Symbol '{qualified_name}' not found"}

        entity_id = f"entity:{project_id}:{sym['file_path']}:{sym['qualified_name']}"

        callers = self.store.get_edges(project_id, target_id=entity_id, rel_type="CALLS")
        caller_details = []
        for c in callers:
            parts = c["source_id"].split(":", 3)
            if len(parts) >= 4:
                caller_sym = self.store.get_symbol(project_id, parts[3])
                if caller_sym:
                    caller_details.append({
                        "function": caller_sym["qualified_name"],
                        "file": caller_sym["file_path"],
                        "line": caller_sym["start_line"],
                        "complexity": (caller_sym.get("complexity") or {}).get("cyclomatic"),
                    })

        callees = self.store.get_edges(project_id, source_id=entity_id, rel_type="CALLS")
        callee_names = [
            c["target_id"].split(":")[-1] for c in callees
        ]

        authored = self.store.get_edges(project_id, source_id=entity_id, rel_type="AUTHORED_BY")
        authors = [e["target_id"].replace("dev:", "") for e in authored]

        return {
            "symbol": sym["qualified_name"],
            "file": sym["file_path"],
            "complexity": sym.get("complexity"),
            "authors": authors,
            "called_by": caller_details,
            "calls": callee_names,
            "impact_radius": len(caller_details),
        }

    def developer_collaboration(self, project_id: str) -> list[dict]:
        edges = self.store.get_edges(project_id, rel_type="COLLABORATES_WITH")
        return [{
            "developer_a": e["source_id"].replace("dev:", ""),
            "developer_b": e["target_id"].replace("dev:", ""),
            "shared_files": e.get("properties", {}).get("shared_files", 0),
            "files": e.get("properties", {}).get("files", []),
        } for e in edges]

    def developer_risk_report(self, project_id: str, email: str) -> dict:
        ownership = self.what_does_developer_own(project_id, email)

        all_authored = self.store.get_edges(project_id, rel_type="AUTHORED_BY")
        file_authors: dict[str, set] = {}
        for e in all_authored:
            parts = e["source_id"].split(":", 3)
            if len(parts) >= 3:
                file_authors.setdefault(parts[2], set()).add(
                    e["target_id"].replace("dev:", "")
                )

        sole_owned = []
        shared = []
        for file_path, symbols in ownership["files"].items():
            authors = file_authors.get(file_path, set())
            if authors == {email}:
                sole_owned.append({"file": file_path, "symbols": symbols})
            else:
                shared.append({
                    "file": file_path,
                    "symbols": symbols,
                    "other_authors": list(authors - {email}),
                })

        complex_owned = []
        for file_path, symbols in ownership["files"].items():
            for qname in symbols:
                sym = self.store.get_symbol(project_id, qname)
                if sym and sym.get("complexity") and sym["complexity"].get("cyclomatic", 0) > 5:
                    complex_owned.append({
                        "function": qname,
                        "file": file_path,
                        "cyclomatic": sym["complexity"]["cyclomatic"],
                        "lines": sym["complexity"].get("lines", 0),
                    })
        complex_owned.sort(key=lambda x: -x["cyclomatic"])

        collab = self.developer_collaboration(project_id)
        partners = [c for c in collab if email in (c["developer_a"], c["developer_b"])]

        return {
            "developer": email,
            "total_files": ownership["files_owned"],
            "total_symbols": ownership["symbols_owned"],
            "sole_owned_files": sole_owned,
            "shared_files": shared,
            "most_complex_code": complex_owned[:10],
            "collaborators": partners,
            "risk_level": (
                "HIGH" if len(sole_owned) > 3
                else "MEDIUM" if len(sole_owned) > 0
                else "LOW"
            ),
        }

    def get_class_structure(self, project_id: str, class_name: str) -> dict:
        class_sym = self.store.get_symbol(project_id, class_name)
        if not class_sym:
            return {"error": f"Class '{class_name}' not found"}

        entity_id = f"entity:{project_id}:{class_sym['file_path']}:{class_sym['qualified_name']}"
        contains = self.store.get_edges(project_id, source_id=entity_id, rel_type="CONTAINS")

        methods = []
        for e in contains:
            parts = e["target_id"].split(":", 3)
            if len(parts) >= 4:
                method_sym = self.store.get_symbol(project_id, parts[3])
                if method_sym:
                    methods.append({
                        "name": method_sym["qualified_name"],
                        "line": method_sym["start_line"],
                        "complexity": (method_sym.get("complexity") or {}).get("cyclomatic"),
                        "calls": method_sym.get("calls", [])[:5],
                    })

        return {
            "class": class_sym["qualified_name"],
            "file": class_sym["file_path"],
            "methods": methods,
            "method_count": len(methods),
        }
