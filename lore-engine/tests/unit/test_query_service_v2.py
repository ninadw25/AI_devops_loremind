"""
tests/unit/test_query_service_v2.py
───────────────────────────────────
End-to-end orchestration of the consolidated QueryService with in-memory fakes
(no Neo4j, no LLM, no models). Proves analyze → (unstructured ∥ jira) → merge →
rank → assemble → synthesize wires together, that both engines run, and that
fail-closed scoping holds at the top level.
"""

from core.settings import Settings
from core.types import RankedDoc, RerankDoc
from services.query_service import QueryService


class FakeGraph:
    available = True
    def __init__(self, rows): self._rows = rows
    def run(self, cypher, **params):
        for pat, r in self._rows.items():
            if pat in cypher:
                return r
        return []


class FakeChat:
    name = "fake"; model = "fake-model"
    def __init__(self, answer="Synthesized answer."): self._a = answer
    # analysis call returns bad JSON → keyword fallback; synthesis returns answer.
    def chat(self, messages, **kw):
        sys = messages[0]["content"] if messages else ""
        if "Output ONLY a JSON" in sys:
            return "not json"   # force keyword fallback (deterministic)
        return self._a
    def chat_stream(self, messages, **kw):
        yield ("content", self._a)


class FakeEmbedder:
    name = "fake"; model = "fake"; dim = 8
    def embed(self, text): return [0.1] * 8


class FakeReranker:
    name = "fake"; model = "fake"
    def rerank(self, query, documents: list[RerankDoc]) -> list[RankedDoc]:
        return [RankedDoc(node_id=d["node_id"], score=1.0) for d in documents]


def _graph_with_results():
    return FakeGraph({
        "queryNodes": [
            {"node_id": "dec:1", "node_type": "Decision",
             "description": "Approved GST migration to v2 API",
             "scope_ids": ["slack:channel:C1"], "source_thread_id": "t1",
             "created_at": "2026-01-01", "score": 0.9},
        ],
        # frontier neighbour fetch from the Decision seed
        "MATCH (n:Decision": [
            {"parent_id": "dec:1", "rel": "BECAUSE", "neighbor_type": "Fact",
             "neighbor_id": "fact:2", "description": "Old API deprecated in March",
             "name": "", "created_at": "", "source_thread_id": "t1"},
        ],
    })


def _svc(graph, reranker=None):
    return QueryService(graph=graph, chat=FakeChat(), embedder=FakeEmbedder(),
                        settings=Settings(), reranker=reranker)


def test_query_end_to_end_returns_answer_and_sources():
    svc = _svc(_graph_with_results(), reranker=FakeReranker())
    out = svc.query("Why did we migrate GST to the v2 API?",
                    user_scopes=["slack:channel:C1"])
    assert out["answer"] == "Synthesized answer."
    assert out["confidence"] in ("low", "medium", "high")
    types = " ".join(s["type"] for s in out["sources"])
    assert "Decision" in types or "Fact" in types
    assert out["stats"]["chunk_count"] >= 1


def test_query_empty_scopes_fail_closed():
    # No scopes → seeds never query, no content, empty response.
    g = _graph_with_results()
    out = _svc(g).query("anything", user_scopes=[])
    assert out["confidence"] == "low"
    assert out["sources"] == []


def test_query_stream_yields_sources_then_tokens_then_done():
    svc = _svc(_graph_with_results(), reranker=FakeReranker())
    events = list(svc.query_stream("Why migrate?", user_scopes=["slack:channel:C1"]))
    kinds = [e["type"] for e in events]
    assert "sources" in kinds and "token" in kinds and kinds[-1] == "done"


def test_query_degrades_without_reranker():
    svc = _svc(_graph_with_results(), reranker=None)
    out = svc.query("Why migrate?", user_scopes=["slack:channel:C1"])
    assert out["answer"] == "Synthesized answer."  # still works, hybrid order


def test_both_engines_run_and_merge():
    # An Issue only reachable via the Jira engine + a Decision via unstructured.
    g = FakeGraph({
        "queryNodes": [
            {"node_id": "dec:1", "node_type": "Decision", "description": "GST v2 decision",
             "scope_ids": ["slack:channel:C1"], "source_thread_id": "t1",
             "created_at": "2026-01-01", "score": 0.9, "text": "GST v2 decision"},
        ],
        "MATCH (n:Decision": [
            {"parent_id": "dec:1", "rel": "BECAUSE", "neighbor_type": "Fact",
             "neighbor_id": "fact:2", "description": "old api deprecated", "name": "",
             "created_at": "", "source_thread_id": "t1", "scope_ids": ["slack:channel:C1"]},
        ],
        # Jira navigational by key → issue lookup + neighbourhood
        "MATCH (i:Issue {key": [
            {"issue_id": "issue:ops-2", "key": "OPS-2", "summary": "Migrate GST",
             "status": "Done", "issue_type": "Task", "scope_ids": ["jira:project:OPS"]},
        ],
        "MATCH (i:Issue {issue_id": [
            {"issue_id": "issue:ops-2", "key": "OPS-2", "summary": "Migrate GST",
             "status": "Done", "issue_type": "Task", "scope_ids": ["jira:project:OPS"],
             "assignee": "Arnav", "reporter": "Reia", "sprint": None,
             "components": [], "decisions": [], "facts": []},
        ],
    })
    svc = _svc(g, reranker=FakeReranker())
    out = svc.query("what is the status of OPS-2 and why GST v2?",
                    user_scopes=["slack:channel:C1", "jira:project:OPS"])
    sources_blob = " ".join(s["type"] + s["thread_id"] for s in out["sources"])
    # both an email/chat node and a Jira issue made it into the merged result
    assert "jira" in sources_blob and ("gmail" in sources_blob or "slack" in sources_blob)
    assert out["stats"]["by_source"]  # diagnostics record per-source counts
