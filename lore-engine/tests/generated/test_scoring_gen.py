import pytest
from domain.scoring import score_confidence, score_confidence_chunks
from core.types import RetrievalResult, SourcedChunk


def make_result(primary=None, supporting=None, persons=None):
    return RetrievalResult(
        primary=primary or [],
        supporting=supporting or [],
        persons=persons or [],
    )


def test_score_confidence_high_via_total():
    # total >= 5 triggers high
    res = make_result(primary=[1, 2], supporting=[3, 4, 5])
    assert score_confidence(res) == "high"


def test_score_confidence_high_via_primary():
    # len(primary) >= 3 triggers high
    res = make_result(primary=[1, 2, 3], supporting=[])
    assert score_confidence(res) == "high"


def test_score_confidence_medium_via_total():
    # total >= 2 triggers medium
    res = make_result(primary=[1], supporting=[2])
    assert score_confidence(res) == "medium"


def test_score_confidence_medium_via_persons():
    # persons truthy triggers medium
    res = make_result(primary=[], supporting=[], persons=["Alice"])
    assert score_confidence(res) == "medium"


def test_score_confidence_low():
    # else low
    res = make_result(primary=[], supporting=[], persons=[])
    assert score_confidence(res) == "low"


def test_score_confidence_chunks_low_empty():
    # n == 0 -> low (line 35)
    assert score_confidence_chunks([]) == "low"


def test_score_confidence_chunks_low():
    # n == 1, top_score < 0.4, not multi_source -> low (line 44)
    chunks = [{"score": 0.3, "source": "src1"}]
    assert score_confidence_chunks(chunks) == "low"


def test_score_confidence_chunks_medium_via_n():
    # n >= 2 -> medium
    chunks = [{"score": 0.1, "source": "src1"}, {"score": 0.2, "source": "src1"}]
    assert score_confidence_chunks(chunks) == "medium"


def test_score_confidence_chunks_medium_via_score():
    # top_score >= 0.4 -> medium
    chunks = [{"score": 0.4, "source": "src1"}]
    assert score_confidence_chunks(chunks) == "medium"


def test_score_confidence_chunks_high_via_n_and_score():
    # n >= 4 and top_score >= 0.5 -> high
    chunks = [
        {"score": 0.5, "source": "src1"},
        {"score": 0.5, "source": "src1"},
        {"score": 0.5, "source": "src1"},
        {"score": 0.5, "source": "src1"},
    ]
    assert score_confidence_chunks(chunks) == "high"


def test_score_confidence_chunks_high_via_multi_source():
    # multi_source and n >= 3 -> high
    chunks = [
        {"score": 0.1, "source": "src1"},
        {"score": 0.1, "source": "src2"},
        {"score": 0.1, "source": "src3"},
    ]
    assert score_confidence_chunks(chunks) == "high"
