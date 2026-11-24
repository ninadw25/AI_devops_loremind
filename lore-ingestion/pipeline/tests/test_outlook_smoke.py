"""Tests for runners.outlook_smoke.run_outlook_smoke_pipeline().

Design:
    - extract_all_fn is always injected so no LLM key or network is needed.
    - All file I/O uses pytest tmp_path.
    - Raw JSONL fixtures are minimal but valid KnowledgeEvent dicts — just
      enough for the filter and registry stages to consume without errors.
    - No Azure credentials required. No Graph API calls are made.

Coverage:
    TestPrerequisites   — missing required raw files raise FileNotFoundError
    TestDiscoveryWarn   — missing discovery artifacts warn but do not abort
    TestMailOnly        — source="outlook_mail" skips Teams stages
    TestTeamsOnly       — source="ms_teams" skips mail stages
    TestBoth            — source="both" runs all stages for both sources
    TestReturnDict      — return dict contains all documented keys
    TestAllowEmpty      — allow_empty=True does not raise on zero events
"""
from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from runners.outlook_smoke import run_outlook_smoke_pipeline


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _outlook_event(conv_id: str = "CONV-1", idx: int = 1) -> dict:
    """Minimal Outlook Mail KnowledgeEvent that passes all filter signals."""
    return {
        "id": f"om-{conv_id}-{idx}",
        "source": "outlook_mail",
        "source_id": f"MSG-{idx}",
        "event_type": "email.message",
        "timestamp": "2026-06-01T10:00:00Z",
        "title": f"Planning meeting {idx}",
        "body": "Let's align on the roadmap for next quarter and set priorities.",
        "primary_actor": "email:alice@corp.com",
        "actors": [
            {"id": "email:alice@corp.com", "email": "alice@corp.com", "display_name": "Alice"},
            {"id": "email:bob@corp.com",   "email": "bob@corp.com",   "display_name": "Bob"},
        ],
        "org": "testorg",
        "raw": {
            "conversation_id": conv_id,
            "isDraft": False,
            "internet_message_id": f"<msg-{idx}@corp.com>",
            "body_content_type": "text",
            "categories": [],
        },
    }


def _teams_event(channel_id: str = "C-1", root_source_id: str = "testorg:T-1:C-1:MSG001") -> dict:
    """Minimal Teams KnowledgeEvent that passes all filter signals."""
    return {
        "id": f"tm-{channel_id}-1",
        "source": "ms_teams",
        "source_id": root_source_id,
        "event_type": "ms_teams.message",
        "timestamp": "2026-06-01T11:00:00Z",
        "title": "Should we move standup to 9 am?",
        "body": "I think morning standups work better. The team seems more focused.",
        "primary_actor": "teams:USER-001",
        "actors": [
            {"id": "teams:USER-001", "display_name": "Alice", "is_bot": False},
        ],
        "org": "testorg",
        "raw": {
            "team_id": "T-1",
            "channel_id": channel_id,
            "scope_id": f"ms365:channel:{channel_id}",
            "reply_to_id": None,
            "message_type": "message",
        },
    }


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def _org_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config" / "testorg_org_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"org_domains": ["corp.com"], "discovered_noise_domains": [],
                    "allowlisted_domains": [], "extra_functional_mailboxes": []}),
        encoding="utf-8",
    )


def _null_extract(result_override: dict | None = None):
    """Mock extract_all_fn: writes an empty triples dict and returns it."""
    async def _fn(
        *,
        filtered_path,
        org,
        data_dir,
        output_path,
        checkpoint_path,
        **kwargs,
    ):
        result = result_override or {}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result), encoding="utf-8")
        return result
    return _fn


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ── TestPrerequisites ─────────────────────────────────────────────────────────

class TestPrerequisites:

    def test_missing_mail_raw_raises_file_not_found(self, tmp_path):
        _org_config(tmp_path)
        with pytest.raises(FileNotFoundError, match="Outlook Mail raw JSONL not found"):
            _run(run_outlook_smoke_pipeline(
                org="testorg",
                output_dir=tmp_path,
                source="outlook_mail",
                allow_empty=True,
                extract_all_fn=_null_extract(),
            ))

    def test_missing_teams_raw_raises_file_not_found(self, tmp_path):
        _org_config(tmp_path)
        with pytest.raises(FileNotFoundError, match="Teams raw JSONL not found"):
            _run(run_outlook_smoke_pipeline(
                org="testorg",
                output_dir=tmp_path,
                source="ms_teams",
                allow_empty=True,
                extract_all_fn=_null_extract(),
            ))

    def test_missing_both_raises_on_first_missing(self, tmp_path):
        """When source='both' and neither file exists, mail error fires first."""
        _org_config(tmp_path)
        with pytest.raises(FileNotFoundError, match="Outlook Mail raw JSONL not found"):
            _run(run_outlook_smoke_pipeline(
                org="testorg",
                output_dir=tmp_path,
                source="both",
                allow_empty=True,
                extract_all_fn=_null_extract(),
            ))

    def test_explicit_raw_mail_path_used_when_provided(self, tmp_path):
        """raw_mail_path override is used instead of OutlookPaths default."""
        _org_config(tmp_path)
        custom = tmp_path / "custom" / "mail.jsonl"
        _write_jsonl(custom, [_outlook_event()])
        # Teams raw is also absent → should fail on Teams, not mail
        with pytest.raises(FileNotFoundError, match="Teams raw JSONL not found"):
            _run(run_outlook_smoke_pipeline(
                org="testorg",
                output_dir=tmp_path,
                raw_mail_path=custom,
                source="both",
                allow_empty=True,
                extract_all_fn=_null_extract(),
            ))

    def test_explicit_raw_teams_path_used_when_provided(self, tmp_path):
        """raw_teams_path override is used instead of OutlookPaths default."""
        _org_config(tmp_path)
        custom = tmp_path / "custom" / "teams.jsonl"
        _write_jsonl(custom, [_teams_event()])
        # Mail raw is absent → should fail on mail
        with pytest.raises(FileNotFoundError, match="Outlook Mail raw JSONL not found"):
            _run(run_outlook_smoke_pipeline(
                org="testorg",
                output_dir=tmp_path,
                raw_teams_path=custom,
                source="both",
                allow_empty=True,
                extract_all_fn=_null_extract(),
            ))


# ── TestDiscoveryWarn ─────────────────────────────────────────────────────────

class TestDiscoveryWarn:
    """Missing discovery artifacts warn but do not abort the pipeline."""

    def test_absent_identities_does_not_raise(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl", [_outlook_event()])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["identities_artifact_present"] is False

    def test_absent_scopes_does_not_raise(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl", [_teams_event()])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="ms_teams",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["scopes_artifact_present"] is False

    def test_present_identities_reported_as_present(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl", [_outlook_event()])
        identities_path = tmp_path / "outlook" / "testorg_ms365_identities.json"
        identities_path.parent.mkdir(parents=True, exist_ok=True)
        identities_path.write_text(
            json.dumps({"org": "testorg", "source": "ms365", "count": 1,
                        "identities": [{"objectId": "x", "displayName": "Alice",
                                        "upn": "alice@corp.com", "email": "alice@corp.com",
                                        "account_type": "member"}]}),
            encoding="utf-8",
        )
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["identities_artifact_present"] is True


# ── TestMailOnly ──────────────────────────────────────────────────────────────

class TestMailOnly:

    def test_mail_only_skips_teams_stages(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event("C1", 1), _outlook_event("C1", 2)])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["source"] == "outlook_mail"
        assert result["filter_mail"] is not None
        assert result["filter_teams"] is None
        assert result["triples_mail_path"] is not None
        assert result["triples_teams_path"] is None

    def test_mail_only_produces_filtered_file(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event("CONV-X", 1)])
        _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        filtered = tmp_path / "filtered" / "testorg_outlook_mail_filtered.jsonl"
        assert filtered.exists()

    def test_mail_only_filter_stats_in_result(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event("C1", 1), _outlook_event("C1", 2)])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert "total_input" in result["filter_mail"]
        assert result["filter_mail"]["total_input"] == 2


# ── TestTeamsOnly ─────────────────────────────────────────────────────────────

class TestTeamsOnly:

    def test_teams_only_skips_mail_stages(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event("C-1", "testorg:T-1:C-1:MSG001")])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="ms_teams",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["source"] == "ms_teams"
        assert result["filter_mail"] is None
        assert result["filter_teams"] is not None
        assert result["triples_mail_path"] is None
        assert result["triples_teams_path"] is not None

    def test_teams_only_produces_filtered_file(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event()])
        _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="ms_teams",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        filtered = tmp_path / "filtered" / "testorg_ms_teams_filtered.jsonl"
        assert filtered.exists()

    def test_teams_only_filter_stats_in_result(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event("C-1"), _teams_event("C-2", "testorg:T-1:C-2:MSG001")])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="ms_teams",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["filter_teams"]["total_input"] == 2


# ── TestBoth ──────────────────────────────────────────────────────────────────

class TestBoth:

    def test_both_runs_all_stages(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event("C1", 1)])
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event()])
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="both",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["source"] == "both"
        assert result["filter_mail"] is not None
        assert result["filter_teams"] is not None
        assert result["triples_mail_path"] is not None
        assert result["triples_teams_path"] is not None

    def test_both_produces_both_filtered_files(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event()])
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event()])
        _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="both",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert (tmp_path / "filtered" / "testorg_outlook_mail_filtered.jsonl").exists()
        assert (tmp_path / "filtered" / "testorg_ms_teams_filtered.jsonl").exists()

    def test_both_calls_extract_all_twice(self, tmp_path):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event()])
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event()])
        call_count = 0

        async def counting_extract(**kwargs):
            nonlocal call_count
            call_count += 1
            kwargs["output_path"].parent.mkdir(parents=True, exist_ok=True)
            kwargs["output_path"].write_text("{}", encoding="utf-8")

        _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="both",
            allow_empty=True,
            extract_all_fn=counting_extract,
        ))
        assert call_count == 2   # once for mail, once for teams


# ── TestReturnDict ────────────────────────────────────────────────────────────

class TestReturnDict:

    def _setup_and_run(self, tmp_path, source="both"):
        _org_config(tmp_path)
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl",
                     [_outlook_event()])
        _write_jsonl(tmp_path / "raw" / "testorg_ms_teams.jsonl",
                     [_teams_event()])
        return _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source=source,
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))

    def test_return_dict_has_all_required_keys(self, tmp_path):
        result = self._setup_and_run(tmp_path)
        required = {
            "org", "source",
            "identities_artifact_present", "scopes_artifact_present",
            "filter_mail", "filter_teams",
            "registry_persons_before_merge", "registry_merge_groups",
            "filtered_mail_path", "filtered_teams_path",
            "triples_mail_path", "triples_teams_path",
            "identities_path", "scopes_path",
        }
        assert required.issubset(result.keys())

    def test_org_and_source_in_result(self, tmp_path):
        result = self._setup_and_run(tmp_path)
        assert result["org"] == "testorg"
        assert result["source"] == "both"

    def test_identities_path_points_to_expected_location(self, tmp_path):
        result = self._setup_and_run(tmp_path)
        assert result["identities_path"].endswith("testorg_ms365_identities.json")

    def test_scopes_path_points_to_expected_location(self, tmp_path):
        result = self._setup_and_run(tmp_path)
        assert result["scopes_path"].endswith("testorg_ms365_teams_scopes.json")

    def test_mail_only_has_none_teams_paths(self, tmp_path):
        result = self._setup_and_run(tmp_path, source="outlook_mail")
        assert result["filtered_teams_path"] is None
        assert result["triples_teams_path"] is None

    def test_teams_only_has_none_mail_paths(self, tmp_path):
        result = self._setup_and_run(tmp_path, source="ms_teams")
        assert result["filtered_mail_path"] is None
        assert result["triples_mail_path"] is None

    def test_registry_counts_are_integers(self, tmp_path):
        result = self._setup_and_run(tmp_path)
        assert isinstance(result["registry_persons_before_merge"], int)
        assert isinstance(result["registry_merge_groups"], int)


# ── TestAllowEmpty ────────────────────────────────────────────────────────────

class TestAllowEmpty:

    def test_allow_empty_true_does_not_raise_on_all_filtered(self, tmp_path):
        """All events are noreply → filter drops everything → allow_empty=True continues."""
        _org_config(tmp_path)
        noise_events = [
            {
                "id": "n1", "source": "outlook_mail", "source_id": "MSG-N1",
                "event_type": "email.message", "timestamp": "2026-06-01T09:00:00Z",
                "title": "Out of Office: vacation",
                "body": "I am out of office.",
                "primary_actor": "email:noreply@corp.com",
                "actors": [],
                "org": "testorg",
                "raw": {"conversation_id": "CONV-NOISE", "isDraft": False,
                        "internet_message_id": "<n1@corp.com>", "body_content_type": "text",
                        "categories": []},
            }
        ]
        _write_jsonl(tmp_path / "raw" / "testorg_outlook_mail.jsonl", noise_events)
        # Should not raise
        result = _run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=True,
            extract_all_fn=_null_extract(),
        ))
        assert result["filter_mail"]["kept"] == 0
