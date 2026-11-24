import pytest
from unittest.mock import Mock
from domain.retrieval.person import (
    resolve_persons,
    expand_person_profile,
    person_scoped_decisions,
    search_people
)

def test_resolve_persons_client_unavailable():
    client = Mock()
    client.available = False
    result = resolve_persons(client, "Any question")
    assert result == []

def test_resolve_persons_no_names():
    client = Mock()
    client.available = True
    client.run.return_value = []
    result = resolve_persons(client, "no names here")
    assert result == []

def test_resolve_persons_single_name_match():
    client = Mock()
    client.available = True
    client.run.return_value = [{"person_id": "1", "name": "John Doe"}]
    result = resolve_persons(client, "Question about John Doe")
    assert len(result) == 1
    assert result[0]["person_id"] == "1"
    assert result[0]["name"] == "John Doe"

def test_resolve_persons_duplicate_names():
    client = Mock()
    client.available = True
    client.run.side_effect = [
        [{"person_id": "1", "name": "John Doe"}],
        [{"person_id": "1", "name": "John Doe"}]
    ]
    result = resolve_persons(client, "John Doe and John Doe")
    assert len(result) == 1
    assert result[0]["person_id"] == "1"

def test_resolve_persons_multiple_different_names():
    client = Mock()
    client.available = True
    client.run.side_effect = [
        [{"person_id": "1", "name": "John Doe"}],
        [{"person_id": "2", "name": "Jane Smith"}]
    ]
    result = resolve_persons(client, "John Doe and Jane Smith")
    assert len(result) == 2
    ids = {p["person_id"] for p in result}
    assert ids == {"1", "2"}

def test_expand_person_profile_no_person():
    client = Mock()
    client.run.return_value = None
    result = expand_person_profile(client, "person_id")
    assert result is None

def test_expand_person_profile_no_org():
    client = Mock()
    client.run.side_effect = [
        [{"person_id": "1", "name": "John Doe"}],
        None
    ]
    result = expand_person_profile(client, "person_id")
    assert result == {"person_id": "1", "name": "John Doe"}

def test_expand_person_profile_with_org():
    client = Mock()
    client.run.side_effect = [
        [{"person_id": "1", "name": "John Doe"}],
        [{"organization": "Acme Corp"}]
    ]
    result = expand_person_profile(client, "person_id")
    assert result == {
        "person_id": "1",
        "name": "John Doe",
        "organization": "Acme Corp"
    }

def test_person_scoped_decisions_no_results():
    client = Mock()
    client.run.return_value = None
    result = person_scoped_decisions(client, "person_id", [0.1, 0.2], ["group1"])
    assert result == []

def test_person_scoped_decisions_with_results():
    client = Mock()
    client.run.return_value = [
        {"decision_id": "d1", "description": "Decision 1", "score": 0.9},
        {"decision_id": "d2", "description": "Decision 2", "score": 0.8}
    ]
    result = person_scoped_decisions(client, "person_id", [0.1, 0.2], ["group1"])
    assert len(result) == 2
    assert result[0]["_expanded"] is True
    assert result[1]["_expanded"] is True
    assert result[0]["score"] == 0.9
    assert result[1]["score"] == 0.8

def test_search_people_empty_fragment():
    client = Mock()
    client.available = True
    assert search_people(client, "") == []
    assert search_people(client, "   ") == []
    assert search_people(client, None) == []

def test_search_people_client_unavailable():
    client = Mock()
    client.available = False
    assert search_people(client, "john") == []

def test_search_people_valid_fragment():
    client = Mock()
    client.available = True
    client.run.return_value = [{"person_id": "1", "name": "John Doe"}]
    result = search_people(client, "john")
    assert len(result) == 1
    assert result[0]["person_id"] == "1"
    assert result[0]["name"] == "John Doe"

def test_search_people_query_returns_none():
    client = Mock()
    client.available = True
    client.run.return_value = None
    result = search_people(client, "john")
    assert result == []
