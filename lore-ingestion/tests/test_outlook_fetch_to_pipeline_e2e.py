"""
tests/test_outlook_fetch_to_pipeline_e2e.py
───────────────────────────────────────────
End-to-end wiring test for the Outlook / Teams connector.

This is the test that was MISSING before the LOR-63 integration fix: every
prior test either fed the normalizer directly or wrote already-normalized
events to the stable raw paths, so the runtime gap between the fetch stage
(which used to write raw Graph payloads) and the filter stage (which expects
normalized KnowledgeEvents) was invisible.

What this verifies, with NO Azure credentials and NO network:
    1. `infra.outlook.run --stage fetch` NORMALIZES raw Microsoft Graph
       payloads into KnowledgeEvents before writing (Gap A).
    2. Fetch writes to the SAME stable paths the smoke/filter stage reads —
       OutlookPaths.raw_mail_jsonl / raw_teams_jsonl (Gap B).
    3. The smoke pipeline (filter → person_registry → extract) then consumes
       that fetch output successfully end to end.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

from infra.outlook import run as outlook_run
from runners.outlook_smoke import run_outlook_smoke_pipeline


# ── Raw Microsoft Graph fixtures (camelCase, exactly as the Graph API returns) ──

def _raw_graph_mail() -> list[dict]:
    return [
        {
            "id": "MSG-1",
            "conversationId": "CONV-1",
            "subject": "Q4 Planning",
            "receivedDateTime": "2026-06-10T09:00:00Z",
            "from": {"emailAddress": {"name": "Alice", "address": "alice@corp.com"}},
            "toRecipients": [{"emailAddress": {"name": "Bob", "address": "bob@corp.com"}}],
            "ccRecipients": [],
            "body": {"contentType": "html",
                     "content": "<p>Let us <b>plan</b> the Q4 roadmap and set team priorities.</p>"},
            "isDraft": False,
            "internetMessageId": "<msg1@corp.com>",
            "categories": [],
        },
        {
            "id": "MSG-2",
            "conversationId": "CONV-1",
            "subject": "Re: Q4 Planning",
            "receivedDateTime": "2026-06-10T09:30:00Z",
            "from": {"emailAddress": {"name": "Bob", "address": "bob@corp.com"}},
            "toRecipients": [{"emailAddress": {"name": "Alice", "address": "alice@corp.com"}}],
            "ccRecipients": [],
            "body": {"contentType": "text",
                     "content": "Sounds good — I will draft the milestones for review."},
            "isDraft": False,
            "internetMessageId": "<msg2@corp.com>",
            "categories": [],
        },
    ]


def _raw_graph_teams() -> list[dict]:
    return [
        {
            "id": "MSG001",
            "createdDateTime": "2026-06-10T10:00:00Z",
            "from": {"user": {"id": "USER-001", "displayName": "Alice"}},
            "body": {"contentType": "html",
                     "content": "<p>I think morning standups work better for team focus.</p>"},
            "channelIdentity": {"teamId": "T-1", "channelId": "C-1"},
            "webUrl": "https://teams.microsoft.com/x/root",
            "replyToId": None,
            "messageType": "message",
        },
    ]


def _channels() -> list[dict]:
    return [{"team_id": "T-1", "team_name": "Engineering",
             "channel_id": "C-1", "channel_name": "deploys"}]


def _users() -> list[dict]:
    return [{"userPrincipalName": "alice@corp.com", "displayName": "Alice"}]


def _write_org_config(tmp_path: Path) -> None:
    cfg = tmp_path / "config" / "testorg_org_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps({"org_domains": ["corp.com"], "discovered_noise_domains": [],
                    "allowlisted_domains": [], "extra_functional_mailboxes": []}),
        encoding="utf-8",
    )


def _null_extract():
    """Inject in place of pipeline.extraction.extract.extract_all (no LLM)."""
    async def _fn(*, filtered_path, org, data_dir, output_path, checkpoint_path, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps({}), encoding="utf-8")
        return {}
    return _fn


def _run_fetch(tmp_path: Path):
    with (
        patch("infra.outlook.run.setup_logging"),
        patch("infra.outlook.run.get_settings"),
        patch("infra.outlook.fetcher.get_access_token", return_value="tok"),
        patch("infra.outlook.run._get_users", return_value=_users()),
        patch("infra.outlook.fetcher.fetch_teams_channels", return_value=_channels()),
        patch("infra.outlook.fetcher.fetch_mail_for_user", return_value=iter(_raw_graph_mail())),
        patch("infra.outlook.fetcher.fetch_channel_messages", return_value=iter(_raw_graph_teams())),
    ):
        sys.argv = ["", "--org", "testorg", "--stage", "fetch", "--output-dir", str(tmp_path)]
        outlook_run.main()


# ── 1. Fetch normalizes raw Graph payloads (Gap A) ────────────────────────────

class TestFetchNormalizes:

    def test_mail_raw_output_is_normalized(self, tmp_path):
        _run_fetch(tmp_path)
        mail_path = tmp_path / "raw" / "testorg_outlook_mail.jsonl"
        assert mail_path.exists(), "fetch must write to the stable OutlookPaths mail path"
        records = [json.loads(l) for l in mail_path.read_text().strip().splitlines()]
        assert len(records) == 2
        for r in records:
            # Normalized KnowledgeEvent shape — NOT raw Graph shape.
            assert r["source"] == "outlook_mail"
            assert r["primary_actor"] == "email:alice@corp.com" or r["primary_actor"] == "email:bob@corp.com"
            # snake_case conversation_id proves the normalizer ran (Graph uses conversationId)
            assert r["raw"]["conversation_id"] == "CONV-1"
            assert "<p>" not in r["body"]            # HTML stripped
            assert "embedding_text" in r             # matches Gmail/Slack raw record shape

    def test_teams_raw_output_is_normalized(self, tmp_path):
        _run_fetch(tmp_path)
        teams_path = tmp_path / "raw" / "testorg_ms_teams.jsonl"
        assert teams_path.exists(), "fetch must write to the stable OutlookPaths teams path"
        records = [json.loads(l) for l in teams_path.read_text().strip().splitlines()]
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "ms_teams"
        assert r["primary_actor"] == "teams:USER-001"
        # scope_id stamped by the normalizer drives Teams ACL downstream
        assert r["raw"]["scope_id"] == "ms365:channel:C-1"
        assert r["raw"]["message_type"] == "message"

    def test_fetch_writes_exactly_two_raw_files(self, tmp_path):
        _run_fetch(tmp_path)
        raw_files = sorted((tmp_path / "raw").glob("*.jsonl"))
        names = {p.name for p in raw_files}
        assert names == {"testorg_outlook_mail.jsonl", "testorg_ms_teams.jsonl"}


# ── 2. Smoke pipeline consumes fetch output end to end (Gap B) ────────────────

class TestFetchThenSmoke:

    def test_filter_consumes_fetch_output(self, tmp_path):
        _run_fetch(tmp_path)
        _write_org_config(tmp_path)

        result = asyncio.run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="both",
            allow_empty=False,
            extract_all_fn=_null_extract(),
        ))

        # The filter received normalized events and kept the real ones.
        assert result["filter_mail"]["kept"] == 2
        assert result["filter_teams"]["kept"] == 1

        # Filtered artifacts exist at the stable filtered paths.
        assert (tmp_path / "filtered" / "testorg_outlook_mail_filtered.jsonl").exists()
        assert (tmp_path / "filtered" / "testorg_ms_teams_filtered.jsonl").exists()

        # Person registry was built from mail actors (alice/bob @ corp.com).
        assert result["registry_persons_before_merge"] >= 1

    def test_noise_is_dropped_after_fetch(self, tmp_path):
        """A draft + a noreply sender fetched as raw Graph must be normalized
        then dropped by the filter — proving the fetch→filter contract end to end."""
        noisy_mail = _raw_graph_mail() + [
            {
                "id": "MSG-NOISE",
                "conversationId": "CONV-2",
                "subject": "Your receipt",
                "receivedDateTime": "2026-06-10T09:45:00Z",
                "from": {"emailAddress": {"name": "No Reply", "address": "no-reply@billing.corp.com"}},
                "toRecipients": [{"emailAddress": {"name": "Alice", "address": "alice@corp.com"}}],
                "body": {"contentType": "text", "content": "Automated billing receipt."},
                "isDraft": False,
                "internetMessageId": "<noise@corp.com>",
                "categories": [],
            },
        ]
        _write_org_config(tmp_path)
        with (
            patch("infra.outlook.run.setup_logging"),
            patch("infra.outlook.run.get_settings"),
            patch("infra.outlook.fetcher.get_access_token", return_value="tok"),
            patch("infra.outlook.run._get_users", return_value=_users()),
            patch("infra.outlook.fetcher.fetch_teams_channels", return_value=[]),
            patch("infra.outlook.fetcher.fetch_mail_for_user", return_value=iter(noisy_mail)),
            patch("infra.outlook.fetcher.fetch_channel_messages", return_value=iter([])),
        ):
            sys.argv = ["", "--org", "testorg", "--stage", "fetch", "--output-dir", str(tmp_path)]
            outlook_run.main()

        result = asyncio.run(run_outlook_smoke_pipeline(
            org="testorg",
            output_dir=tmp_path,
            source="outlook_mail",
            allow_empty=False,
            extract_all_fn=_null_extract(),
        ))
        # 3 fetched, the noreply one dropped → 2 kept.
        assert result["filter_mail"]["total_input"] == 3
        assert result["filter_mail"]["kept"] == 2
        assert result["filter_mail"]["dropped"] == 1
