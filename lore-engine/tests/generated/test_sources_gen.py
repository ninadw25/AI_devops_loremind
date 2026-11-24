import pytest
from domain.sources import format_sources, format_sources_chunks

class RetrievalResult:
    def __init__(self, primary, supporting):
        self.primary = primary
        self.supporting = supporting

def test_format_sources_person_node():
    node = {
        "decisions_made": True,
        "person_id": "p1",
        "name": "John Doe"
    }
    result = RetrievalResult(primary=[node], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    src = sources[0]
    assert src.type == "Person"
    assert src.description == "John Doe"
    assert src.thread_id == ""
    assert src.score == 1.0
    assert src.sensitivity == "internal"

def test_format_sources_person_node_duplicate():
    node1 = {
        "decisions_made": True,
        "person_id": "p1",
        "name": "John"
    }
    node2 = {
        "decisions_made": True,
        "person_id": "p1",
        "name": "Jane"
    }
    result = RetrievalResult(primary=[node1, node2], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    assert sources[0].description == "John"

def test_format_sources_person_node_fallback_name():
    node = {
        "decisions_made": True,
        "name": "Alice"
    }
    result = RetrievalResult(primary=[node], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    src = sources[0]
    assert src.type == "Person"
    assert src.description == "Alice"
    assert src.thread_id == ""
    assert src.score == 1.0
    assert src.sensitivity == "internal"

def test_format_sources_normal_node():
    node = {
        "node_id": "n1",
        "type": "Document",
        "description": "Test doc",
        "source_thread_id": "thread123",
        "score": "0.75",
        "sensitivity_label": "confidential"
    }
    result = RetrievalResult(primary=[node], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    src = sources[0]
    assert src.type == "Document"
    assert src.description == "Test doc"
    assert src.thread_id == "thread123"
    assert src.score == 0.75
    assert src.sensitivity == "confidential"

def test_format_sources_normal_node_fallback_description():
    node = {
        "node_id": "n2",
        "type": "Task",
        "task": "Complete project",
        "thread_id": "thread456"
    }
    result = RetrievalResult(primary=[node], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    src = sources[0]
    assert src.type == "Task"
    assert src.description == "Complete project"
    assert src.thread_id == "thread456"
    assert src.score == 0.0
    assert src.sensitivity == "internal"

def test_format_sources_normal_node_duplicate():
    node1 = {
        "node_id": "n1",
        "type": "A",
        "description": "First"
    }
    node2 = {
        "node_id": "n1",
        "type": "B",
        "description": "Second"
    }
    result = RetrievalResult(primary=[node1, node2], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    assert sources[0].description == "First"

def test_format_sources_normal_node_with_thread_id():
    node = {
        "node_id": "n1",
        "type": "Document",
        "description": "Test",
        "thread_id": "n1",
        "score": "0.5"
    }
    result = RetrievalResult(primary=[node], supporting=[])
    sources = format_sources(result)
    assert len(sources) == 1
    src = sources[0]
    assert src.thread_id == "n1"

def test_format_sources_limits():
    primary = [{"node_id": f"p{i}"} for i in range(10)]
    supporting = [{"node_id": f"s{i}"} for i in range(10)]
    result = RetrievalResult(primary=primary, supporting=supporting)
    sources = format_sources(result)
    assert len(sources) == 10  # 6 primary + 4 supporting

def test_format_sources_mixed():
    primary = [
        {"decisions_made": True, "person_id": "p1", "name": "Person"},
        {"node_id": "n1", "type": "Doc", "description": "Doc1"}
    ]
    supporting = [
        {"node_id": "s1", "type": "Doc", "description": "Doc2"}
    ]
    result = RetrievalResult(primary=primary, supporting=supporting)
    sources = format_sources(result)
    assert len(sources) == 3
    types = [s.type for s in sources]
    assert "Person" in types
    assert "Doc" in types

def test_format_sources_chunks_basic():
    chunks = [{
        "node_id": "c1",
        "node_type": "Email",
        "source": "gmail",
        "text": "Hello world",
        "score": "0.8",
        "citations": ["cite1"]
    }]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 1
    src = sources[0]
    assert src.type == "Email (gmail)"
    assert src.description == "Hello world"
    assert src.thread_id == "cite1"
    assert src.score == 0.8
    assert src.sensitivity == "internal"

def test_format_sources_chunks_limit():
    chunks = [{"node_id": str(i)} for i in range(15)]
    sources = format_sources_chunks(chunks, limit=5)
    assert len(sources) == 5

def test_format_sources_chunks_skip_empty_nid():
    chunks = [
        {"node_id": ""},
        {"node_id": "c1", "text": "Text"}
    ]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 1
    assert sources[0].description == "Text"

def test_format_sources_chunks_skip_duplicate_nid():
    chunks = [
        {"node_id": "c1", "text": "First"},
        {"node_id": "c1", "text": "Second"}
    ]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 1
    assert sources[0].description == "First"

def test_format_sources_chunks_default_limit():
    chunks = [{"node_id": str(i)} for i in range(15)]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 10

def test_format_sources_chunks_thread_id_from_citations():
    chunks = [{
        "node_id": "c1",
        "text": "Text",
        "citations": ["email123"]
    }]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 1
    assert sources[0].thread_id == "email123"

def test_format_sources_chunks_thread_id_fallback():
    chunks = [{
        "node_id": "c1",
        "text": "Text",
        "citations": [""]
    }]
    sources = format_sources_chunks(chunks)
    assert len(sources) == 1
    assert sources[0].thread_id == ""
