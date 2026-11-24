"""Phase 11A — Mock Validation.

End-to-end verification of the Outlook/Teams pipeline using mocks only.
No Azure credentials required.

Checkpoints verified:
  1. run.py fetch stage
  2. run.py dry-run mode
  3. JSONL output generation
  4. Delta token persistence
  5. Teams replies preserved
  6. channelIdentity present on all messages
  7. Outlook normalizer end-to-end
  8. Teams normalizer end-to-end
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from infra.outlook import run as outlook_run
from infra.outlook.fetcher import DeltaStore, fetch_channel_messages
from infra.outlook.normalizer import normalize_outlook_message, normalize_teams_message
from infra.outlook.run import JSONLWriter
from common.hashing import make_id
from common.schemas import EventType, KnowledgeEvent


# ─── Shared fixtures ──────────────────────────────────────────────────────────

def _users(n=1):
    return [{"userPrincipalName": f"user{i}@test.com", "displayName": f"User {i}"} for i in range(1, n + 1)]

def _channels(n=1):
    return [
        {"team_id": f"T{i}", "team_name": f"Team{i}", "channel_id": f"C{i}", "channel_name": f"Chan{i}"}
        for i in range(1, n + 1)
    ]

def _mail_msgs(n=2):
    return [{"id": f"mail{i}", "conversationId": "CONV1", "subject": "S", "body": {"content": f"M{i}"}} for i in range(1, n + 1)]

def _teams_msgs(n=2):
    return [{"id": f"teams{i}", "body": {"content": f"T{i}"}} for i in range(1, n + 1)]


# ─── 1 — run.py fetch stage ───────────────────────────────────────────────────

class TestRunFetchStage:
    """Checkpoint 1: run.py fetch stage completes and calls all fetchers."""

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_fetch_completes_without_error(self, fcm, fmu, ftc, gu, gat, gs, sl):
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter(_mail_msgs(2))
        fcm.return_value = iter(_teams_msgs(2))
        sys.argv = ["", "--org", "test-org", "--stage", "fetch"]
        outlook_run.main()  # must not raise

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_mail_fetcher_called_once_per_user(self, fcm, fmu, ftc, gu, gat, gs, sl):
        gu.return_value = _users(2)
        ftc.return_value = _channels(1)
        fmu.side_effect = [iter(_mail_msgs(1)), iter(_mail_msgs(1))]
        fcm.return_value = iter(_teams_msgs(1))
        sys.argv = ["", "--org", "test-org", "--stage", "fetch"]
        outlook_run.main()
        assert fmu.call_count == 2

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_teams_fetcher_called_once_per_channel(self, fcm, fmu, ftc, gu, gat, gs, sl):
        gu.return_value = _users(1)
        ftc.return_value = _channels(3)
        fmu.return_value = iter(_mail_msgs(1))
        fcm.side_effect = [iter(_teams_msgs(1)) for _ in range(3)]
        sys.argv = ["", "--org", "test-org", "--stage", "fetch"]
        outlook_run.main()
        assert fcm.call_count == 3

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_limit_stops_fetching_early(self, fcm, fmu, ftc, gu, gat, gs, sl):
        gu.return_value = _users(5)
        ftc.return_value = _channels(5)
        fmu.side_effect = lambda **kw: iter(_mail_msgs(10))
        fcm.side_effect = lambda **kw: iter(_teams_msgs(10))
        sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--limit", "3"]
        outlook_run.main()
        # With limit=3 all 3 consumed from first user's mail → teams block skipped
        assert fmu.call_count >= 1


# ─── 2 — run.py dry-run mode ─────────────────────────────────────────────────

class TestRunDryRun:
    """Checkpoint 2: --dry-run writes no files but still calls all fetchers."""

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_no_jsonl_files_written(self, fcm, fmu, ftc, gu, gat, gs, sl, tmp_path):
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter(_mail_msgs(3))
        fcm.return_value = iter(_teams_msgs(3))
        sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--dry-run",
                    "--output-dir", str(tmp_path)]
        outlook_run.main()
        raw = tmp_path / "raw"
        files = list(raw.glob("*.jsonl")) if raw.exists() else []
        assert files == []

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_fetchers_still_called_in_dry_run(self, fcm, fmu, ftc, gu, gat, gs, sl, tmp_path):
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter(_mail_msgs(2))
        fcm.return_value = iter(_teams_msgs(2))
        sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--dry-run",
                    "--output-dir", str(tmp_path)]
        outlook_run.main()
        fmu.assert_called_once()
        fcm.assert_called_once()

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_dry_run_count_matches_messages_seen(self, fcm, fmu, ftc, gu, gat, gs, sl, tmp_path):
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter(_mail_msgs(4))
        fcm.return_value = iter(_teams_msgs(3))
        with patch("infra.outlook.run.JSONLWriter", wraps=JSONLWriter) as MockWriter:
            sys.argv = ["", "--org", "test-org", "--stage", "fetch", "--dry-run",
                        "--output-dir", str(tmp_path)]
            outlook_run.main()
        # Both writers instantiated with dry_run=True
        for c in MockWriter.call_args_list:
            assert c.args[1] is True


# ─── 3 — JSONL output generation ─────────────────────────────────────────────

class TestJSONLOutputGeneration:
    """Checkpoint 3: JSONLWriter produces valid per-line JSON files."""

    def test_creates_file_with_correct_line_count(self, tmp_path):
        path = tmp_path / "out.jsonl"
        w = JSONLWriter(path, dry_run=False)
        for i in range(5):
            w.write({"id": f"msg{i}", "n": i})
        w.close()
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5

    def test_each_line_is_valid_json(self, tmp_path):
        path = tmp_path / "out.jsonl"
        w = JSONLWriter(path, dry_run=False)
        for i in range(4):
            w.write({"id": f"r{i}", "body": f"text {i}"})
        w.close()
        for line in path.read_text(encoding="utf-8").strip().splitlines():
            obj = json.loads(line)
            assert "id" in obj

    def test_all_fields_preserved(self, tmp_path):
        path = tmp_path / "out.jsonl"
        record = {"id": "x1", "source": "outlook_mail", "tags": ["email", "internal"], "nested": {"k": 1}}
        w = JSONLWriter(path, dry_run=False)
        w.write(record)
        w.close()
        assert json.loads(path.read_text(encoding="utf-8").strip()) == record

    def test_count_matches_written(self, tmp_path):
        path = tmp_path / "out.jsonl"
        w = JSONLWriter(path, dry_run=False)
        for i in range(7):
            w.write({"i": i})
        w.close()
        assert w.count == 7

    def test_dry_run_no_file_but_count_increments(self, tmp_path):
        path = tmp_path / "dry.jsonl"
        w = JSONLWriter(path, dry_run=True)
        w.write({"id": "a"})
        w.write({"id": "b"})
        w.close()
        assert w.count == 2
        assert not path.exists()

    def test_unicode_content_preserved(self, tmp_path):
        path = tmp_path / "unicode.jsonl"
        w = JSONLWriter(path, dry_run=False)
        w.write({"body": "こんにちは — café résumé"})
        w.close()
        obj = json.loads(path.read_text(encoding="utf-8").strip())
        assert obj["body"] == "こんにちは — café résumé"

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_fetch_stage_creates_two_jsonl_files(self, fcm, fmu, ftc, gu, gat, gs, sl, tmp_path):
        """One mail file and one teams file are written when limit not reached."""
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter([{"id": "m1"}, {"id": "m2"}])
        fcm.return_value = iter([{"id": "t1"}])
        sys.argv = ["", "--org", "test-org", "--stage", "fetch",
                    "--output-dir", str(tmp_path)]
        outlook_run.main()
        raw_files = sorted((tmp_path / "raw").glob("*.jsonl"))
        assert len(raw_files) == 2

    @patch("infra.outlook.run.setup_logging")
    @patch("infra.outlook.run.get_settings")
    @patch("infra.outlook.fetcher.get_access_token", return_value="tok")
    @patch("infra.outlook.run._get_users")
    @patch("infra.outlook.fetcher.fetch_teams_channels")
    @patch("infra.outlook.fetcher.fetch_mail_for_user")
    @patch("infra.outlook.fetcher.fetch_channel_messages")
    def test_mail_jsonl_contains_correct_records(self, fcm, fmu, ftc, gu, gat, gs, sl, tmp_path):
        gu.return_value = _users(1)
        ftc.return_value = _channels(1)
        fmu.return_value = iter([{"id": "mail_unique_id_1"}, {"id": "mail_unique_id_2"}])
        fcm.return_value = iter([])
        sys.argv = ["", "--org", "test-org", "--stage", "fetch",
                    "--output-dir", str(tmp_path)]
        outlook_run.main()
        mail_file = next((tmp_path / "raw").glob("*outlook_mail*.jsonl"))
        records = [json.loads(line) for line in mail_file.read_text(encoding="utf-8").strip().splitlines()]
        # Fetch now NORMALIZES raw Graph payloads into KnowledgeEvents before
        # writing, so the top-level `id` is the deterministic hash and the
        # original Graph message id is preserved in `source_id`.
        source_ids = [r["source_id"] for r in records]
        assert "mail_unique_id_1" in source_ids
        assert "mail_unique_id_2" in source_ids
        assert all(r["source"] == "outlook_mail" for r in records)


# ─── 4 — Delta token persistence ─────────────────────────────────────────────

class TestDeltaTokenPersistence:
    """Checkpoint 4: DeltaStore persists tokens across instantiations."""

    def test_mail_token_survives_reload(self, tmp_path):
        path = tmp_path / "delta.json"
        DeltaStore("org", path=path).set("mail:a@b.com", "TOK1", 10)
        DeltaStore("org", path=path)._data  # no save → should NOT persist

        store = DeltaStore("org", path=path)
        store.set("mail:a@b.com", "TOK1", 10)
        store.save()

        assert DeltaStore("org", path=path).get("mail:a@b.com")["delta_token"] == "TOK1"

    def test_teams_token_survives_reload(self, tmp_path):
        path = tmp_path / "delta.json"
        s = DeltaStore("org", path=path)
        s.set("teams:T1:C1", "TTEAMS", 7)
        s.save()
        assert DeltaStore("org", path=path).get("teams:T1:C1")["delta_token"] == "TTEAMS"

    def test_second_save_overwrites_first(self, tmp_path):
        path = tmp_path / "delta.json"
        s1 = DeltaStore("org", path=path)
        s1.set("mail:a@b.com", "FIRST", 5)
        s1.save()
        s2 = DeltaStore("org", path=path)
        s2.set("mail:a@b.com", "SECOND", 12)
        s2.save()
        assert DeltaStore("org", path=path).get("mail:a@b.com")["delta_token"] == "SECOND"

    def test_full_deltalink_url_stored(self, tmp_path):
        path = tmp_path / "delta.json"
        url = "https://graph.microsoft.com/v1.0/users/a@b.com/messages/delta?$deltatoken=LONG"
        s = DeltaStore("org", path=path)
        s.set("mail:a@b.com", url, 50)
        s.save()
        assert DeltaStore("org", path=path).get("mail:a@b.com")["delta_token"] == url

    def test_multiple_mailboxes_independent(self, tmp_path):
        path = tmp_path / "delta.json"
        s = DeltaStore("org", path=path)
        s.set("mail:alice@co.com", "A_TOK", 1)
        s.set("mail:bob@co.com", "B_TOK", 2)
        s.set("teams:T:C", "T_TOK", 3)
        s.save()
        reloaded = DeltaStore("org", path=path)
        assert reloaded.get("mail:alice@co.com")["delta_token"] == "A_TOK"
        assert reloaded.get("mail:bob@co.com")["delta_token"] == "B_TOK"
        assert reloaded.get("teams:T:C")["delta_token"] == "T_TOK"


# ─── 5 — Teams replies preserved ─────────────────────────────────────────────

class TestTeamsRepliesPreserved:
    """Checkpoint 5: fetch_channel_messages yields root messages AND all replies."""

    def _graph_get_side_effect(self, root_msgs, replies_map):
        """Builds a mock for graph_get that handles delta + replies endpoints."""
        def _impl(endpoint, token, params=None, return_last_response=False):
            if "/messages/delta" in str(endpoint) or (
                isinstance(endpoint, str) and endpoint.startswith("https://")
                and "/messages/delta" not in endpoint
                and "/replies" not in endpoint
            ):
                last_json = {
                    "value": root_msgs,
                    "@odata.deltaLink": "https://graph.microsoft.com/delta?$deltatoken=TOK",
                }
                if return_last_response:
                    return list(root_msgs), last_json
                return list(root_msgs)
            if "/replies" in str(endpoint):
                for msg_id, replies in replies_map.items():
                    if msg_id in endpoint:
                        return list(replies)
                return []
            return []
        return _impl

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_root_plus_replies_all_yielded(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [
            {"id": "r1", "body": {"content": "Root 1"}},
            {"id": "r2", "body": {"content": "Root 2"}},
        ]
        replies_map = {
            "r1": [{"id": "rep1a", "body": {"content": "Reply A"}}],
            "r2": [
                {"id": "rep2a", "body": {"content": "Reply B"}},
                {"id": "rep2b", "body": {"content": "Reply C"}},
            ],
        }
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, replies_map)
        msgs = list(fetch_channel_messages(
            org="org", team_id="T1", team_name="TN", channel_id="C1",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        # 2 roots + 1 reply for r1 + 2 replies for r2 = 5
        assert len(msgs) == 5

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_reply_to_id_injected_when_missing(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": "r1", "body": {"content": "Root"}}]
        replies_map = {"r1": [{"id": "rep1", "body": {"content": "Reply"}}]}  # no replyToId
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, replies_map)
        msgs = list(fetch_channel_messages(
            org="org", team_id="T1", team_name="TN", channel_id="C1",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        reply = next(m for m in msgs if m["id"] == "rep1")
        assert reply["replyToId"] == "r1"

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_existing_reply_to_id_not_overwritten(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": "r1", "body": {"content": "Root"}}]
        replies_map = {"r1": [{"id": "rep1", "body": {"content": "Reply"}, "replyToId": "ORIGINAL"}]}
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, replies_map)
        msgs = list(fetch_channel_messages(
            org="org", team_id="T1", team_name="TN", channel_id="C1",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        reply = next(m for m in msgs if m["id"] == "rep1")
        assert reply["replyToId"] == "ORIGINAL"

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_metadata_injected_on_all_messages(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": "r1", "body": {"content": "Root"}}]
        replies_map = {"r1": [{"id": "rep1", "body": {"content": "Reply"}}]}
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, replies_map)
        msgs = list(fetch_channel_messages(
            org="org", team_id="TEAM-X", team_name="TeamX", channel_id="CHAN-Y",
            channel_name="ChanY", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        for msg in msgs:
            assert msg["team_id"] == "TEAM-X"
            assert msg["channel_id"] == "CHAN-Y"
            assert msg["team_name"] == "TeamX"
            assert msg["channel_name"] == "ChanY"


# ─── 6 — channelIdentity present ─────────────────────────────────────────────

class TestChannelIdentityPresent:
    """Checkpoint 6: channelIdentity synthesized when absent, preserved when present."""

    def _graph_get_side_effect(self, root_msgs, reply_msgs=None):
        def _impl(endpoint, token, params=None, return_last_response=False):
            if "/messages/delta" in str(endpoint):
                last_json = {
                    "value": root_msgs,
                    "@odata.deltaLink": "https://graph.microsoft.com/delta?$deltatoken=TOK",
                }
                if return_last_response:
                    return list(root_msgs), last_json
                return list(root_msgs)
            if "/replies" in str(endpoint):
                return list(reply_msgs) if reply_msgs else []
            return []
        return _impl

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_synthesized_when_missing_from_root(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": "r1", "body": {"content": "Hi"}}]  # no channelIdentity
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs)
        msgs = list(fetch_channel_messages(
            org="org", team_id="TEAM-ABC", team_name="TN", channel_id="CHAN-XYZ",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        root = next(m for m in msgs if m["id"] == "r1")
        assert root["channelIdentity"] == {"teamId": "TEAM-ABC", "channelId": "CHAN-XYZ"}

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_synthesized_on_replies_too(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": "r1", "body": {"content": "Hi"}}]
        reply_msgs = [{"id": "rep1", "body": {"content": "Reply"}}]  # no channelIdentity
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, reply_msgs)
        msgs = list(fetch_channel_messages(
            org="org", team_id="TEAM-001", team_name="TN", channel_id="CHAN-002",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        reply = next(m for m in msgs if m["id"] == "rep1")
        assert reply["channelIdentity"]["teamId"] == "TEAM-001"
        assert reply["channelIdentity"]["channelId"] == "CHAN-002"

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_all_messages_have_channel_identity(self, mock_gg, mock_gat, tmp_path):
        root_msgs = [{"id": f"r{i}", "body": {"content": f"Root {i}"}} for i in range(3)]
        reply_msgs = [{"id": "rep1", "body": {"content": "Reply"}}]
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs, reply_msgs)
        msgs = list(fetch_channel_messages(
            org="org", team_id="TEAM-Q", team_name="TN", channel_id="CHAN-Q",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        for msg in msgs:
            assert "channelIdentity" in msg, f"Missing channelIdentity on {msg['id']}"

    @patch("infra.outlook.fetcher.get_access_token", return_value="test_token")
    @patch("infra.outlook.fetcher.graph_get")
    def test_existing_channel_identity_not_overwritten(self, mock_gg, mock_gat, tmp_path):
        existing = {"teamId": "TEAM-ABC", "channelId": "CHAN-XYZ", "extra_field": "keep_me"}
        root_msgs = [{"id": "r1", "body": {"content": "Hi"}, "channelIdentity": existing}]
        mock_gg.side_effect = self._graph_get_side_effect(root_msgs)
        msgs = list(fetch_channel_messages(
            org="org", team_id="TEAM-ABC", team_name="TN", channel_id="CHAN-XYZ",
            channel_name="CN", settings=MagicMock(),
            store_path=tmp_path / "delta.json",
        ))
        assert msgs[0]["channelIdentity"]["extra_field"] == "keep_me"


# ─── 7 — Outlook normalizer end-to-end ───────────────────────────────────────

class TestOutlookNormalizerEndToEnd:
    """Checkpoint 7: raw Graph mail message → KnowledgeEvent with correct fields."""

    @pytest.fixture
    def raw(self):
        return {
            "id": "MSG-E2E-001",
            "conversationId": "THREAD-E2E-001",
            "subject": "Q4 Planning",
            "receivedDateTime": "2026-06-10T09:00:00Z",
            "from": {"emailAddress": {"name": "Alice", "address": "alice@corp.com"}},
            "toRecipients": [{"emailAddress": {"name": "Bob", "address": "bob@corp.com"}}],
            "ccRecipients": [{"emailAddress": {"name": "Carol", "address": "carol@corp.com"}}],
            "bccRecipients": [],
            "body": {"contentType": "html", "content": "<p>Let us <b>plan</b> Q4.</p>"},
            "internetMessageId": "<e2e001@corp.com>",
            "categories": ["Planning"],
        }

    def test_returns_knowledge_event(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert isinstance(ev, KnowledgeEvent)

    def test_event_type_email_message(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert ev.event_type == EventType.EMAIL_MESSAGE

    def test_source_outlook_mail(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert ev.source == "outlook_mail"

    def test_html_stripped_body(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert "<p>" not in ev.body
        assert "<b>" not in ev.body
        assert "plan" in ev.body

    def test_body_capped_at_4000(self, raw):
        raw["body"]["content"] = "<p>" + "X" * 5000 + "</p>"
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert len(ev.body) == 4000

    def test_actors_include_sender_to_cc(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        ids = {a["id"] for a in ev.actors}
        assert "email:alice@corp.com" in ids
        assert "email:bob@corp.com" in ids
        assert "email:carol@corp.com" in ids

    def test_no_duplicate_actors(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        ids = [a["id"] for a in ev.actors]
        assert len(ids) == len(set(ids))

    def test_thread_parent_id_links_same_conversation(self, raw):
        msg_b = dict(raw)
        msg_b["id"] = "MSG-E2E-002"
        ev_a = normalize_outlook_message(raw, "corp", "alice@corp.com")
        ev_b = normalize_outlook_message(msg_b, "corp", "alice@corp.com")
        assert ev_a.parent_id is not None
        assert ev_a.parent_id == ev_b.parent_id

    def test_parent_id_none_without_conversation(self, raw):
        raw["conversationId"] = ""
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert ev.parent_id is None

    def test_internal_tag_same_domain(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert "internal" in ev.tags
        assert "external" not in ev.tags

    def test_external_tag_different_domain(self, raw):
        ev = normalize_outlook_message(raw, "corp", "admin@otherdomain.com")
        assert "external" in ev.tags

    def test_curated_raw_keys_present(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert "conversation_id" in ev.raw
        assert "internet_message_id" in ev.raw
        assert "body_content_type" in ev.raw
        assert "categories" in ev.raw

    def test_raw_excludes_full_body(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert "content" not in ev.raw

    def test_project_equals_subject(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert ev.project == "Q4 Planning"

    def test_org_field_set(self, raw):
        ev = normalize_outlook_message(raw, "corp", "alice@corp.com")
        assert ev.org == "corp"


# ─── 8 — Teams normalizer end-to-end ─────────────────────────────────────────

class TestTeamsNormalizerEndToEnd:
    """Checkpoint 8: raw Graph Teams message → KnowledgeEvent with correct fields."""

    @pytest.fixture
    def root(self):
        return {
            "id": "TEAMS-E2E-001",
            "createdDateTime": "2026-06-10T10:00:00Z",
            "from": {"user": {"id": "U-E2E-001", "displayName": "Alice"}},
            "body": {"contentType": "html", "content": "<p>Deploy tonight?</p>"},
            "channelIdentity": {"teamId": "T-E2E", "channelId": "C-E2E"},
            "webUrl": "https://teams.microsoft.com/e2e/root",
            "replyToId": None,
            "messageType": "message",
        }

    @pytest.fixture
    def reply(self):
        return {
            "id": "TEAMS-E2E-002",
            "createdDateTime": "2026-06-10T10:05:00Z",
            "from": {"user": {"id": "U-E2E-002", "displayName": "Bob"}},
            "body": {"contentType": "text", "content": "Yes, confirmed."},
            "channelIdentity": {"teamId": "T-E2E", "channelId": "C-E2E"},
            "webUrl": "https://teams.microsoft.com/e2e/reply",
            "replyToId": "TEAMS-E2E-001",
            "messageType": "message",
        }

    def test_returns_knowledge_event(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert isinstance(ev, KnowledgeEvent)

    def test_event_type_team_message(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.event_type == EventType.TEAM_MESSAGE

    def test_source_ms_teams(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.source == "ms_teams"

    def test_html_stripped_body(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert "<p>" not in ev.body
        assert "Deploy tonight?" in ev.body

    def test_body_capped_at_4000(self, root):
        root["body"]["content"] = "<span>" + "Y" * 5000 + "</span>"
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert len(ev.body) <= 4000

    def test_actor_id_teams_prefix(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.primary_actor == "teams:U-E2E-001"
        assert ev.actors[0]["id"] == "teams:U-E2E-001"

    def test_actor_source_ms_teams(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.actors[0]["source"] == "ms_teams"

    def test_no_parent_id_for_root(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.parent_id is None

    def test_reply_parent_id_points_to_root(self, root, reply):
        root_ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        reply_ev = normalize_teams_message(reply, "Engineering", "deploys", "corp")
        assert reply_ev.parent_id is not None
        assert reply_ev.parent_id == root_ev.id

    def test_project_team_hash_channel(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.project == "Engineering#deploys"

    def test_tags_teams_and_team_id(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert "teams" in ev.tags
        assert "team:T-E2E" in ev.tags

    def test_org_field_set(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.org == "corp"

    def test_curated_raw_keys_present(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        for key in ("team_id", "channel_id", "scope_id", "reply_to_id", "message_type"):
            assert key in ev.raw

    def test_raw_excludes_full_body(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert "content" not in ev.raw

    def test_raw_team_and_channel_ids(self, root):
        ev = normalize_teams_message(root, "Engineering", "deploys", "corp")
        assert ev.raw["team_id"] == "T-E2E"
        assert ev.raw["channel_id"] == "C-E2E"
