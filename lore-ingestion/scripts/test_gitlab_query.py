"""
test_gitlab_query.py
────────────────────
Emulate a user querying the GitLab/chronograph pipeline end-to-end against the
freshly loaded lore_data store: structured analytics, semantic code search,
function trace, and full LLM chat.

Fixes two config gaps vs the engine's default chronograph_config:
  - inject NVIDIA keys so semantic_search._embed_query works (index/query same model),
  - override openrouter_llm_model to a valid OpenRouter id (the default is a Bedrock id).
"""
from __future__ import annotations
import json
from pathlib import Path

from chronograph.store.store_factory import make_store
from chronograph.query.query_engine import QueryEngine
from chronograph.config import CHRONOGRAPH_CONFIG
from common.config import get_settings

BAR = "=" * 74


def main() -> int:
    s = get_settings()
    store = make_store(s)
    assert store.connect()

    cfg = dict(CHRONOGRAPH_CONFIG)
    cfg["nvidia_api_key"] = s.nvidia_api_key
    cfg["embedding_model"] = s.embedding_model
    cfg["nvidia_base_url"] = s.nvidia_base_url
    # LLM synthesis via AWS Bedrock — same provider/model as the Gmail pipeline.
    env = {}
    for line in Path("/home/zeiko/work/loremind/lore/lore-engine/.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    cfg["llm_provider"] = "bedrock"
    cfg["bedrock_api_key"] = env.get("BEDROCK_API_KEY", "")
    cfg["bedrock_region"] = env.get("BEDROCK_REGION", "ap-south-1")
    cfg["bedrock_model"] = env.get("LLM_MODEL", "apac.anthropic.claude-3-5-sonnet-20241022-v2:0")

    eng = QueryEngine(store, cfg)
    pid = store.get_stats("makarandKate/gmc-server")["project_id"]

    print(f"{BAR}\n1. STRUCTURED — top contributors\n{BAR}")
    for a in eng.get_people(pid)[:5]:
        print(f"  {a['name']:22} {a['commit_count']:4} commits  +{a['additions']}/-{a['deletions']}")

    print(f"\n{BAR}\n2. STRUCTURED — hotspots / bus-factor / complexity\n{BAR}")
    print("  hotspots:")
    for h in eng.get_hotspots(pid, limit=5):
        print(f"    {h['change_count']:3}x  {h['file_path']}")
    print("  bus-factor risks (sole-author files):")
    for b in eng.get_bus_factor(pid, min_changes=5)[:5]:
        print(f"    {b['total_changes']:3} changes  {b['file_path']}  -> {b['sole_author']}")
    print("  most complex functions:")
    for c in eng.get_complexity(pid, limit=5):
        print(f"    cyclomatic={c['complexity']['cyclomatic']:3}  {c['qualified_name']}  ({c['file_path']})")

    print(f"\n{BAR}\n3. SEMANTIC CODE SEARCH (pgvector ANN over 453 symbol embeddings)\n{BAR}")
    for q in ["send a push notification to a user",
              "verify authentication token / login",
              "database connection setup"]:
        print(f"  Q: {q!r}")
        for r in eng.semantic_search(pid, q, top_k=3):
            print(f"     [{r['score']:.3f}] {r['kind']:8} {r['qualified_name']}  ({r['file_path']})")
        print()

    print(f"{BAR}\n4. FUNCTION TRACE (call graph from edges)\n{BAR}")
    hit = eng.semantic_search(pid, "send a push notification to a user", top_k=1)
    if hit:
        tr = eng.trace_function(pid, hit[0]["qualified_name"])
        print(f"  {tr.get('function')} @ {tr.get('file')}:{tr.get('line_range')}")
        print(f"    calls ({len(tr.get('calls', []))}): {tr.get('calls', [])[:6]}")
        print(f"    called_by ({len(tr.get('called_by', []))}): "
              f"{[c['qualified_name'] for c in tr.get('called_by', [])][:6]}")

    print(f"\n{BAR}\n5. CHAT — full pipeline (structured + semantic + LLM synthesis)\n{BAR}")
    ans = eng.chat(pid, "Who are the main contributors and what does the notification "
                        "code do? Any bus-factor risks?")
    if isinstance(ans, dict):
        print("  answer:\n")
        print("   " + (ans.get("answer") or ans.get("error") or json.dumps(ans))[:1600].replace("\n", "\n   "))
    else:
        print("  ", str(ans)[:1600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
