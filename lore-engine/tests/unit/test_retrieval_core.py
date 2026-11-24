"""
tests/unit/test_retrieval_core.py
─────────────────────────────────
seeds / frontier / rank behaviour against a fake graph + fake reranker.
No live Neo4j, no models. Scope/source gates are enforced in Cypher (asserted
structurally here); these tests cover the Python orchestration: seeding,
beam expansion bookkeeping (visited set, convergence, beam prune, paths),
and hybrid+rerank+budget ranking.
"""

from core.settings import Settings
from core.types import BeamCandidate, QueryAnalysis, RankedDoc, RerankDoc, UserScopes
from domain.retrieval import frontier, rank, seeds


# ── Fakes ─────────────────────────────────────────────────────────────

class FakeGraph:
    """Returns pre-set rows per Cypher substring; records calls."""
    available = True

    def __init__(self, rows: dict[str, list[dict]]):
        self._rows = rows
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher: str, **params):
        self.calls.append((cypher, params))
        for pattern, rows in self._rows.items():
            if pattern in cypher:
                return rows
        return []


class Unavailable:
    available = False
    def run(self, *a, **k): return []


class FakeReranker:
    name = "fake"; model = "fake"
    def __init__(self, order: list[str]):  # node_ids best→worst
        self._order = order
    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]:
        rank_of = {nid: len(self._order) - i for i, nid in enumerate(self._order)}
        return sorted(
            [RankedDoc(node_id=d["node_id"], score=float(rank_of.get(d["node_id"], 0)))
             for d in documents],
            key=lambda r: r.score, reverse=True,
        )


SCOPES = UserScopes(["slack:channel:C1"])
SETTINGS = Settings()


# ── seeds ─────────────────────────────────────────────────────────────

def test_vector_seeds_empty_scopes_fail_closed():
    g = FakeGraph({"queryNodes": [{"node_id": "dec:1", "node_type": "Decision",
                                    "description": "x", "score": 0.9}]})
    assert seeds.vector_seeds(g, [0.0] * 8, UserScopes([]), k=10) == []
    assert not g.calls  # never even queried — fail-closed before IO


def test_vector_seeds_builds_candidates():
    g = FakeGraph({"queryNodes": [
        {"node_id": "dec:1", "node_type": "Decision", "description": "approved X",
         "scope_ids": ["slack:channel:C1"], "source_thread_id": "t1",
         "created_at": "2026-01-01", "score": 0.91},
    ]})
    out = seeds.vector_seeds(g, [0.1] * 8, SCOPES, k=10)
    assert len(out) == 1 and out[0].node_id == "dec:1"
    assert out[0].hop == 0 and out[0].score == 0.91 and out[0].path == [("SEED", "dec:1")]


def test_entity_anchors_score_one_and_dedup():
    g = FakeGraph({"MATCH (p:Person)": [{"node_id": "person:1", "name": "Priya", "node_type": "Person"}]})
    a = seeds.entity_anchors(g, QueryAnalysis(intent="PERSON_LOOKUP", persons=["Priya"]), SCOPES)
    assert len(a) == 1 and a[0].score == 1.0 and a[0].labels == ["Person"]


def test_merge_seeds_dedups_vector_first():
    v = [BeamCandidate(node_id="dec:1", originating_seed="dec:1", score=0.8, hop=0, labels=["Decision"])]
    anch = [BeamCandidate(node_id="dec:1", originating_seed="dec:1", score=1.0, hop=0, labels=["Decision"])]
    merged = seeds.merge_seeds(v, anch)
    assert len(merged) == 1 and merged[0].score == 0.8  # vector wins (kept its cosine)


# ── frontier ──────────────────────────────────────────────────────────

def _seed(nid="dec:1", label="Decision", score=0.9):
    return BeamCandidate(node_id=nid, originating_seed=nid, score=score, hop=0,
                         labels=[label], path=[("SEED", nid)], node_type=label)


def test_expand_unavailable_returns_seeds():
    out, diag = frontier.expand(Unavailable(), [_seed()], QueryAnalysis(), SCOPES)
    assert [c.node_id for c in out] == ["dec:1"]


def test_expand_one_hop_adds_neighbor_with_path():
    g = FakeGraph({"MATCH (n:Decision": [
        {"parent_id": "dec:1", "rel": "BECAUSE", "neighbor_type": "Fact",
         "neighbor_id": "fact:9", "description": "because reasons",
         "name": "", "created_at": "", "source_thread_id": "t1"},
    ]})
    out, diag = frontier.expand(g, [_seed()], QueryAnalysis(intent="REASONING"),
                                SCOPES, max_depth=1)
    ids = {c.node_id for c in out}
    assert "fact:9" in ids
    fact = next(c for c in out if c.node_id == "fact:9")
    assert fact.hop == 1 and fact.path[-1] == ("BECAUSE", "fact:9")
    assert fact.score > 0  # REASONING boosts BECAUSE


def test_expand_skips_visited_seed():
    # neighbour equal to the seed must not be re-added
    g = FakeGraph({"MATCH (n:Decision": [
        {"parent_id": "dec:1", "rel": "REGARDING", "neighbor_type": "Decision",
         "neighbor_id": "dec:1", "description": "", "name": "", "created_at": "",
         "source_thread_id": ""},
    ]})
    out, _ = frontier.expand(g, [_seed()], QueryAnalysis(), SCOPES, max_depth=1)
    assert [c.node_id for c in out].count("dec:1") == 1


def test_expand_convergence_bonus():
    # two parents reach the same neighbour → path_count 2, bonus applied
    g = FakeGraph({"MATCH (n:Decision": [
        {"parent_id": "dec:1", "rel": "REGARDING", "neighbor_type": "Fact",
         "neighbor_id": "fact:X", "description": "", "name": "", "created_at": "", "source_thread_id": ""},
        {"parent_id": "dec:2", "rel": "REGARDING", "neighbor_type": "Fact",
         "neighbor_id": "fact:X", "description": "", "name": "", "created_at": "", "source_thread_id": ""},
    ]})
    seeds_in = [_seed("dec:1"), _seed("dec:2")]
    out, _ = frontier.expand(g, seeds_in, QueryAnalysis(), SCOPES, max_depth=1)
    fx = next(c for c in out if c.node_id == "fact:X")
    assert fx.path_count == 2 and fx.convergence_bonus > 1.0


# ── rank_chunks (unified, operates on merged SourcedChunk pool) ───────

def _chunk(nid, text, score, source="gmail", ntype="Decision"):
    return {"source": source, "node_id": nid, "node_type": ntype, "text": text,
            "score": score, "scope_ids": [], "citations": [], "created_at": ""}


def test_rank_uses_reranker_order_across_sources():
    chunks = [_chunk("dec:1", "weather", 0.95, source="gmail"),
              _chunk("issue:2", "gst payment", 0.5, source="jira", ntype="Issue")]
    # reranker prefers the Jira issue despite lower engine score
    res = rank.rank_chunks("gst payment", chunks, FakeReranker(["issue:2", "dec:1"]),
                           settings=SETTINGS)
    assert res.chunks[0]["node_id"] == "issue:2"
    assert res.diagnostics["reranked"] is True
    assert res.diagnostics["by_source"] == {"jira": 1, "gmail": 1}


def test_rank_degrades_without_reranker():
    chunks = [_chunk("dec:1", "alpha", 0.9), _chunk("dec:2", "beta", 0.4)]
    res = rank.rank_chunks("x", chunks, None, settings=SETTINGS)
    assert res.diagnostics["reranked"] is False
    assert {c["node_id"] for c in res.chunks} == {"dec:1", "dec:2"}


def test_rank_sets_final_score_on_chunk():
    chunks = [_chunk("dec:1", "gst", 0.9)]
    res = rank.rank_chunks("gst", chunks, FakeReranker(["dec:1"]), settings=SETTINGS)
    assert isinstance(res.chunks[0]["score"], float)


def test_rank_token_budget_caps_output():
    s = Settings(context_token_budget=20)  # tiny budget → only first chunk fits
    big = "word " * 200
    chunks = [_chunk("dec:1", big, 0.9), _chunk("dec:2", big, 0.8)]
    res = rank.rank_chunks("word", chunks, None, settings=s)
    assert len(res.chunks) == 1


def test_rank_empty_pool():
    res = rank.rank_chunks("anything", [], FakeReranker([]), settings=SETTINGS)
    assert res.chunks == []
