"""
pipeline/tests/test_builder_ms365.py
──────────────────────────────────────
Builder dry-run tests for Outlook Mail and Microsoft Teams sources.

These tests verify that unified_builder.py correctly:
  - Discovers {org}_outlook_mail_triples_*.json files
  - Discovers {org}_ms_teams_triples_*.json files
  - Generates valid Cypher output for both sources (skip_upload=True)
  - Propagates scope_ids from MS365 triples into Cypher SET statements

No Neo4j connection is made (skip_upload=True).
No LLM API key required.
No Azure credentials required.

Fixture data mirrors the structure produced by the smoke runner's mock
extract_all_fn — minimal but structurally correct triples dicts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from builders.unified_builder import _discover_triples, run_builder


# ── Fixtures ──────────────────────────────────────────────────────────────────

ORG = "testorg"


def _mail_thread(conv_id: str, scope_ids: list[str]) -> dict:
    """Minimal Outlook Mail thread dict with participant scope_ids."""
    return {
        "thread_id": conv_id,
        "scope_ids": scope_ids,        # OR-bucket: participant person_ids
        "scope_id": scope_ids[0] if scope_ids else None,
        "extra_scope_ids": [],
        "triples": [
            {
                "subject": {
                    "type": "Decision",
                    "node_id": f"dec:{conv_id}:1",
                    "name": "Approved Q3 roadmap",
                    "description": "We approved the Q3 roadmap",
                },
                "predicate": "DECIDED_BY",
                "object": {
                    "type": "Person",
                    "node_id": "person:alice@corp.com",
                    "name": "Alice Smith",
                },
                "evidence_sentence": "Alice approved the Q3 roadmap in the planning meeting.",
                "derived": "llm",
            }
        ],
        "thread_summary": "Q3 roadmap approved.",
        "department_tags": ["general"],
        "sensitivity": "internal",
        "email_count": 2,
    }


def _teams_thread(thread_id: str, channel_scope: str) -> dict:
    """Minimal Teams thread dict with ms365:channel: scope_id."""
    return {
        "thread_id": thread_id,
        "scope_id": channel_scope,     # ms365:channel:{channelId}
        "scope_ids": [],
        "extra_scope_ids": [],
        "triples": [
            {
                "subject": {
                    "type": "Fact",
                    "node_id": f"fact:{thread_id}:1",
                    "name": "Auth module review complete",
                    "description": "Code review for auth module complete",
                },
                "predicate": "DISCUSSED_IN",
                "object": {
                    "type": "Topic",
                    "node_id": "topic:code-review",
                    "name": "Code Review",
                },
                "evidence_sentence": "Code review for auth module is complete and ready to merge.",
                "derived": "llm",
            }
        ],
        "thread_summary": "Auth module review complete.",
        "department_tags": ["engineering"],
        "sensitivity": "internal",
        "email_count": 1,
    }


def _registry(data_dir: Path) -> None:
    """Write a minimal person registry so load_inputs() can proceed."""
    reg_dir = data_dir / "knowledge" / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    registry = {
        "persons": [
            {
                "person_id": "person:alice@corp.com",
                "canonical_name": "Alice Smith",
                "primary_email": "alice@corp.com",
                "all_emails": ["alice@corp.com"],
                "all_names": ["Alice Smith"],
                "is_internal": True,
                "is_functional": False,
                "domains": ["corp.com"],
                "stats": {
                    "emails_sent": 5, "emails_received": 3,
                    "total_activity": 8, "threads_participated": 2,
                    "unique_contacts": 1,
                },
            }
        ],
        "email_to_person_id": {"alice@corp.com": "person:alice@corp.com"},
    }
    (reg_dir / f"{ORG}_person_registry.json").write_text(
        json.dumps(registry), encoding="utf-8")


def _write_triples(data_dir: Path, source: str, threads: dict) -> Path:
    """Write a triples_deduped file for the given source."""
    triples_dir = data_dir / "knowledge" / "triples"
    triples_dir.mkdir(parents=True, exist_ok=True)
    if source == "gmail":
        fname = f"{ORG}_triples_deduped.json"
    else:
        fname = f"{ORG}_{source}_triples_deduped.json"
    path = triples_dir / fname
    path.write_text(json.dumps(threads), encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# _discover_triples() — file discovery
# ═════════════════════════════════════════════════════════════════════════════

class TestBuilderDiscovery:

    def test_discovers_outlook_mail_triples_file(self, tmp_path):
        """Builder discovers {org}_outlook_mail_triples_deduped.json."""
        threads = {"CONV-1": _mail_thread("CONV-1", ["person:alice@corp.com"])}
        _write_triples(tmp_path, "outlook_mail", threads)

        result = _discover_triples(ORG, tmp_path, source="outlook_mail")

        assert "CONV-1" in result, f"CONV-1 not found. Keys: {list(result)}"
        assert result["CONV-1"]["scope_ids"] == ["person:alice@corp.com"]

    def test_discovers_ms_teams_triples_file(self, tmp_path):
        """Builder discovers {org}_ms_teams_triples_deduped.json."""
        tid = "testorg:T-1:C-1:MSG001"
        threads = {tid: _teams_thread(tid, "ms365:channel:C-1")}
        _write_triples(tmp_path, "ms_teams", threads)

        result = _discover_triples(ORG, tmp_path, source="ms_teams")

        assert tid in result, f"Teams thread not found. Keys: {list(result)}"
        assert result[tid]["scope_id"] == "ms365:channel:C-1"

    def test_multi_source_merge_includes_both_ms365_sources(self, tmp_path):
        """Multi-source build (source=None) merges both MS365 triples files."""
        mail_threads = {"CONV-1": _mail_thread("CONV-1", ["person:alice@corp.com"])}
        tid = "testorg:T-1:C-1:MSG001"
        teams_threads = {tid: _teams_thread(tid, "ms365:channel:C-1")}
        _write_triples(tmp_path, "outlook_mail", mail_threads)
        _write_triples(tmp_path, "ms_teams",     teams_threads)

        result = _discover_triples(ORG, tmp_path, source=None)

        assert "CONV-1" in result, "Outlook Mail thread not in multi-source merge"
        assert tid      in result, "Teams thread not in multi-source merge"

    def test_discovery_returns_empty_when_no_files_present(self, tmp_path):
        """_discover_triples returns {} gracefully when no triples files exist."""
        (tmp_path / "knowledge" / "triples").mkdir(parents=True, exist_ok=True)
        result = _discover_triples(ORG, tmp_path, source=None)
        assert result == {}

    def test_discovery_uses_deduped_over_validated(self, tmp_path):
        """When both deduped and validated files exist, deduped takes precedence."""
        triples_dir = tmp_path / "knowledge" / "triples"
        triples_dir.mkdir(parents=True, exist_ok=True)
        deduped  = {"CONV-DEDUP": _mail_thread("CONV-DEDUP", ["person:alice@corp.com"])}
        validated = {"CONV-VALID": _mail_thread("CONV-VALID", ["person:bob@corp.com"])}
        (triples_dir / f"{ORG}_outlook_mail_triples_deduped.json").write_text(
            json.dumps(deduped), encoding="utf-8")
        (triples_dir / f"{ORG}_outlook_mail_triples_validated.json").write_text(
            json.dumps(validated), encoding="utf-8")

        result = _discover_triples(ORG, tmp_path, source=None)

        assert "CONV-DEDUP"  in result, "Deduped thread not found"
        assert "CONV-VALID" not in result, "Validated file should be skipped when deduped exists"


# ═════════════════════════════════════════════════════════════════════════════
# run_builder() — dry-run Cypher generation (skip_upload=True)
# ═════════════════════════════════════════════════════════════════════════════

class TestBuilderDryRun:
    """Verify that run_builder() with skip_upload=True produces valid Cypher
    files for both MS365 sources without connecting to Neo4j."""

    def test_dry_run_outlook_mail_produces_cypher_files(self, tmp_path):
        """Builder skip_upload run for outlook_mail produces pass0, pass1,
        pass2 Cypher files."""
        _registry(tmp_path)
        threads = {"CONV-1": _mail_thread("CONV-1", ["person:alice@corp.com"])}
        _write_triples(tmp_path, "outlook_mail", threads)

        run_builder(
            org=ORG,
            data_dir=tmp_path,
            skip_upload=True,
            allow_empty=True,
            source="outlook_mail",
        )

        cypher_dir = tmp_path / "cypher"
        assert cypher_dir.exists(), "cypher/ directory not created"
        schema  = cypher_dir / f"{ORG}_pass0_schema.cypher"
        persons = cypher_dir / f"{ORG}_pass1_persons.cypher"
        corr    = cypher_dir / f"{ORG}_pass2_corroborates.cypher"
        assert schema.exists(),  "pass0_schema.cypher not created"
        assert persons.exists(), "pass1_persons.cypher not created"
        assert corr.exists(),    "pass2_corroborates.cypher not created"
        # At least one pass2 chunk should be produced
        chunks = list(cypher_dir.glob(f"{ORG}_pass2_chunk_*.cypher"))
        assert len(chunks) >= 1, f"No pass2 knowledge chunks created. Files: {list(cypher_dir.iterdir())}"

    def test_dry_run_ms_teams_produces_cypher_files(self, tmp_path):
        """Builder skip_upload run for ms_teams produces pass0, pass1, pass2 files."""
        _registry(tmp_path)
        tid = "testorg:T-1:C-1:MSG001"
        threads = {tid: _teams_thread(tid, "ms365:channel:C-1")}
        _write_triples(tmp_path, "ms_teams", threads)

        run_builder(
            org=ORG,
            data_dir=tmp_path,
            skip_upload=True,
            allow_empty=True,
            source="ms_teams",
        )

        cypher_dir = tmp_path / "cypher"
        assert cypher_dir.exists()
        assert (cypher_dir / f"{ORG}_pass0_schema.cypher").exists()
        assert (cypher_dir / f"{ORG}_pass1_persons.cypher").exists()
        chunks = list(cypher_dir.glob(f"{ORG}_pass2_chunk_*.cypher"))
        assert len(chunks) >= 1

    def test_outlook_mail_scope_ids_appear_in_cypher(self, tmp_path):
        """scope_ids list from Outlook Mail threads appears in the generated Cypher."""
        _registry(tmp_path)
        threads = {
            "CONV-1": _mail_thread("CONV-1", [
                "person:alice@corp.com", "person:bob@corp.com"
            ])
        }
        _write_triples(tmp_path, "outlook_mail", threads)

        run_builder(
            org=ORG,
            data_dir=tmp_path,
            skip_upload=True,
            allow_empty=True,
            source="outlook_mail",
        )

        cypher_dir = tmp_path / "cypher"
        all_cypher = "\n".join(
            p.read_text(encoding="utf-8")
            for p in cypher_dir.glob(f"{ORG}_pass2_chunk_*.cypher")
        )
        assert "person:alice@corp.com" in all_cypher, \
            "alice's person_id not found in Cypher scope_ids"
        assert "person:bob@corp.com" in all_cypher, \
            "bob's person_id not found in Cypher scope_ids"

    def test_teams_ms365_scope_id_appears_in_cypher(self, tmp_path):
        """ms365:channel:{channelId} from Teams threads appears in generated Cypher."""
        _registry(tmp_path)
        tid = "testorg:T-1:C-1:MSG001"
        threads = {tid: _teams_thread(tid, "ms365:channel:C-1")}
        _write_triples(tmp_path, "ms_teams", threads)

        run_builder(
            org=ORG,
            data_dir=tmp_path,
            skip_upload=True,
            allow_empty=True,
            source="ms_teams",
        )

        cypher_dir = tmp_path / "cypher"
        all_cypher = "\n".join(
            p.read_text(encoding="utf-8")
            for p in cypher_dir.glob(f"{ORG}_pass2_chunk_*.cypher")
        )
        assert "ms365:channel:C-1" in all_cypher, \
            "ms365:channel:C-1 scope_id not found in Teams Cypher output"

    def test_multi_source_dry_run_produces_output_for_both(self, tmp_path):
        """Multi-source build (source=None) processes both MS365 sources and
        produces Cypher containing scope values from both."""
        _registry(tmp_path)
        mail_threads = {
            "CONV-1": _mail_thread("CONV-1", ["person:alice@corp.com"])
        }
        tid = "testorg:T-1:C-1:MSG001"
        teams_threads = {tid: _teams_thread(tid, "ms365:channel:C-1")}
        _write_triples(tmp_path, "outlook_mail", mail_threads)
        _write_triples(tmp_path, "ms_teams",     teams_threads)

        run_builder(
            org=ORG,
            data_dir=tmp_path,
            skip_upload=True,
            allow_empty=True,
            source=None,   # multi-source merge
        )

        cypher_dir = tmp_path / "cypher"
        all_cypher = "\n".join(
            p.read_text(encoding="utf-8")
            for p in cypher_dir.glob(f"{ORG}_pass2_chunk_*.cypher")
        )
        assert "person:alice@corp.com" in all_cypher, \
            "Outlook Mail scope_id not in multi-source Cypher"
        assert "ms365:channel:C-1" in all_cypher, \
            "Teams scope_id not in multi-source Cypher"
