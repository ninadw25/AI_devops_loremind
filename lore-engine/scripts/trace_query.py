"""
scripts/trace_query.py
──────────────────────
Ad-hoc pipeline tracer: runs the v2 query pipeline stage-by-stage against the
live Neo4j + providers and prints the intermediate output of each stage.

Runs as a content super-admin, which is what makes the scope-less ezobooks
corpus visible (its nodes have no scope_ids, so a normal caller sees nothing).
This mirrors a real request from a caller holding `content.read_all` — it is not
a special tracer-only path.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone

from core.settings import get_settings
from infra.neo4j_client import Neo4jClient
from infra.llm import build_chat_provider
from infra.embeddings import build_embedding_provider
from core.types import UserScopes
from domain.analysis import analyze_query
from domain.retrieval.seeds import vector_seeds, entity_anchors, merge_seeds
from domain.retrieval.frontier import expand
from domain.retrieval.ontology import CONTENT_LABELS
from domain.retrieval.unstructured import _attribution, _render_chunk
from domain.retrieval.rank import rank_chunks
from domain.context import build_context_chunks
from domain.prompts import SYNTHESIS_SYSTEM_PROMPT, build_user_message, scrub_phones
from domain.scoring import score_confidence_chunks

QUESTION = "What is the PIP process and who made it and why?"
BAR = "=" * 78

def hdr(n, title):
    print(f"\n{BAR}\nSTAGE {n}: {title}\n{BAR}")

def main():
    s = get_settings()
    graph = Neo4jClient(s.neo4j_uri, s.neo4j_username, s.neo4j_password)
    chat = build_chat_provider(s)
    embedder = build_embedding_provider(s)
    print(f"graph.available = {graph.available} | llm = {chat.name}/{chat.model} | embed = {embedder.name}/{embedder.model}")
    scopes = UserScopes([], super_admin=True)  # read everything, as content.read_all does
    k = s.vector_seed_top_k

    print(f"\nQUESTION: {QUESTION!r}")

    # ── STAGE 1: Intent analysis (LLM) ──────────────────────────────
    hdr(1, "INTENT ANALYSIS (analyze_query — 1 small LLM call)")
    now_iso = datetime.now(timezone.utc).date().isoformat()
    analysis = analyze_query(chat, QUESTION, now_iso=now_iso)
    print(json.dumps({
        "intent": analysis.intent,
        "persons": analysis.persons,
        "orgs": analysis.orgs,
        "topics": analysis.topics,
        "time_range": analysis.time_range,
    }, indent=2, default=str))

    # ── STAGE 2: Embedding ──────────────────────────────────────────
    hdr(2, "EMBEDDING (question → vector)")
    emb = embedder.embed(QUESTION)
    print(f"dim = {len(emb)} | first 6 = {[round(x,4) for x in emb[:6]]}")

    # ── STAGE 3: Seeds (vector + entity anchors → merged) ───────────
    hdr(3, "SEED RETRIEVAL (vector_seeds + entity_anchors → merge)")
    vseeds = vector_seeds(graph, emb, scopes, k=k, time_range=analysis.time_range)
    anchors = entity_anchors(graph, analysis, scopes)
    seeds = merge_seeds(vseeds, anchors)
    print(f"vector_seeds = {len(vseeds)} | entity_anchors = {len(anchors)} | merged = {len(seeds)}")
    for c in seeds[:8]:
        print(f"  - {c.node_type:9} score={c.score:.3f} {str(c.description)[:70]!r}")

    # ── STAGE 4: Frontier/beam graph expansion (ontology traversal) ─
    hdr(4, "GRAPH EXPANSION (frontier beam over ontology — spine + content)")
    candidates, diag = expand(graph, seeds, analysis, scopes,
                              beam_width=s.frontier_beam_width,
                              max_depth=s.frontier_max_depth)
    persons = [c for c in candidates if c.node_type == "Person"]
    content = [c for c in candidates if c.node_type in CONTENT_LABELS]
    print(f"beam_width={s.frontier_beam_width} max_depth={s.frontier_max_depth}")
    print(f"total candidates = {len(candidates)} | content = {len(content)} | person(spine) = {len(persons)}")
    print(f"diag = {json.dumps(diag, default=str)[:300]}")
    print("  sample content nodes reached:")
    for c in content[:6]:
        print(f"    - {c.node_type:9} score={c.score:.3f} depth={len(c.path)} {str(c.description)[:60]!r}")

    # ── STAGE 5: Render + unified rank ──────────────────────────────
    hdr(5, "RENDER + RANK (ontology attribution → cross-encoder/hybrid rank)")
    attribution = _attribution(persons)
    print(f"attribution edges resolved: {sum(len(v) for v in attribution.values())} across {len(attribution)} nodes")
    chunks = [_render_chunk(c, attribution) for c in candidates if c.node_type in CONTENT_LABELS]
    result = rank_chunks(QUESTION, chunks, None, settings=s)
    print(f"chunks in = {len(chunks)} | ranked out = {len(result.chunks)}")
    print(f"rank diagnostics = {json.dumps(result.diagnostics, default=str)[:300]}")
    for c in result.chunks[:6]:
        print(f"    [{c.get('score',0):.3f}] {c.get('node_type'):9} {c.get('text','')[:90]!r}")

    # ── STAGE 6: Context assembly ───────────────────────────────────
    hdr(6, "CONTEXT ASSEMBLY (chunks → LLM context block)")
    context = build_context_chunks(result.chunks)
    print(f"context length = {len(context)} chars")
    print("---- first 1200 chars ----")
    print(context[:1200])

    # ── STAGE 7: LLM synthesis ──────────────────────────────────────
    hdr(7, "LLM SYNTHESIS (Bedrock Claude 3.5 Sonnet)")
    messages = [
        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
        {"role": "user", "content": build_user_message(QUESTION, scrub_phones(context))},
    ]
    answer = scrub_phones(chat.chat(messages, enable_thinking=True))
    confidence = score_confidence_chunks(result.chunks)
    print(f"confidence = {confidence}")
    print("---- ANSWER ----")
    print(answer)
    graph.close()

if __name__ == "__main__":
    main()
