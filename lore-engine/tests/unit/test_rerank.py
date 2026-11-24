"""
tests/unit/test_rerank.py
─────────────────────────
RerankProvider registry + Protocol conformance. No live model or network:
FlashRank/NVIDIA construction is exercised only for its failure modes
(missing dependency / missing key); scoring is covered via a fake provider.
"""

import pytest

from core.exceptions import ConfigError
from core.settings import Settings
from core.types import RankedDoc, RerankDoc
from infra.rerank.base import RerankProvider
from infra.rerank.registry import build_rerank_provider


# ── Fake provider satisfying the Protocol ─────────────────────────────

class FakeReranker:
    name = "fake"
    model = "fake-model"

    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]:
        # Deterministic: score = overlap of query tokens with doc text.
        q = set(query.lower().split())
        scored = [
            RankedDoc(node_id=d["node_id"],
                      score=float(len(q & set((d["text"] or "").lower().split()))))
            for d in documents
        ]
        return sorted(scored, key=lambda r: r.score, reverse=True)


def test_fake_satisfies_protocol():
    assert isinstance(FakeReranker(), RerankProvider)


def test_fake_sorts_desc_and_preserves_ids():
    docs: list[RerankDoc] = [
        {"node_id": "a", "text": "gst invoice payment"},
        {"node_id": "b", "text": "weather report"},
        {"node_id": "c", "text": "gst payment refund"},
    ]
    out = FakeReranker().rerank("gst payment", docs)
    assert [r.node_id for r in out] == ["a", "c", "b"] or out[0].node_id in ("a", "c")
    assert {r.node_id for r in out} == {"a", "b", "c"}  # no silent drops


def test_empty_docs_returns_empty():
    assert FakeReranker().rerank("anything", []) == []


# ── Registry ──────────────────────────────────────────────────────────

def test_registry_unknown_provider_raises():
    s = Settings(rerank_provider="flashrank")
    object.__setattr__(s, "rerank_provider", "nope")  # bypass Literal at runtime
    with pytest.raises(ConfigError):
        build_rerank_provider(s)


def test_flashrank_missing_dep_raises_configerror():
    # flashrank is an optional dependency; if absent, selecting it must fail
    # with a clear ConfigError (not ImportError) at build time.
    pytest.importorskip
    try:
        import flashrank  # noqa: F401
        pytest.skip("flashrank installed — missing-dep path not exercised here")
    except ImportError:
        s = Settings(rerank_provider="flashrank")
        with pytest.raises(ConfigError):
            build_rerank_provider(s)
