"""Unit tests for DeltaStore in infra/outlook/fetcher.py."""
import json
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from infra.outlook.fetcher import DeltaStore


class TestMissingFile:
    def test_missing_file_starts_empty(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        assert store._data == {}

    def test_get_on_empty_store_returns_none(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        assert store.get("mail:user@test.com") is None


class TestSetAndSave:
    def test_set_stores_token(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("mail:user@test.com", "TOKEN123", 42)
        rec = store.get("mail:user@test.com")
        assert rec is not None
        assert rec["delta_token"] == "TOKEN123"
        assert rec["message_count"] == 42

    def test_set_records_last_sync_timestamp(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("mail:user@test.com", "TOKEN123", 5)
        assert "last_sync" in store._data["mail:user@test.com"]

    def test_save_creates_file(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user@test.com", "TOKEN123", 10)
        store.save()
        assert path.exists()

    def test_save_writes_valid_json(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user@test.com", "TOKEN123", 5)
        store.save()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "mail:user@test.com" in data
        assert data["mail:user@test.com"]["delta_token"] == "TOKEN123"
        assert data["mail:user@test.com"]["message_count"] == 5

    def test_overwrite_existing_key(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("mail:user@test.com", "OLD_TOKEN", 5)
        store.set("mail:user@test.com", "NEW_TOKEN", 15)
        rec = store.get("mail:user@test.com")
        assert rec["delta_token"] == "NEW_TOKEN"
        assert rec["message_count"] == 15

    def test_save_is_idempotent(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user@test.com", "TOKEN", 1)
        store.save()
        store.save()
        assert path.exists()


class TestLoad:
    def test_load_reads_existing_file(self, tmp_path):
        path = tmp_path / "tokens.json"
        data = {
            "mail:user@test.com": {
                "delta_token": "SAVED_TOKEN",
                "last_sync": "2026-01-01T00:00:00+00:00",
                "message_count": 99,
            }
        }
        path.write_text(json.dumps(data), encoding="utf-8")

        store = DeltaStore("test-org", path=path)
        rec = store.get("mail:user@test.com")
        assert rec is not None
        assert rec["delta_token"] == "SAVED_TOKEN"
        assert rec["message_count"] == 99

    def test_round_trip_save_and_reload(self, tmp_path):
        path = tmp_path / "tokens.json"
        store1 = DeltaStore("test-org", path=path)
        store1.set("teams:t1:c1", "TEAM_TOKEN", 7)
        store1.save()

        store2 = DeltaStore("test-org", path=path)
        rec = store2.get("teams:t1:c1")
        assert rec is not None
        assert rec["delta_token"] == "TEAM_TOKEN"
        assert rec["message_count"] == 7


class TestAtomicWrite:
    def test_no_tmp_files_after_save(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user@test.com", "TOKEN", 1)
        store.save()
        leftover = list(tmp_path.glob("*.tmp"))
        assert len(leftover) == 0

    def test_final_file_is_correct_json(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user@test.com", "TOKEN_A", 3)
        store.save()
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["mail:user@test.com"]["delta_token"] == "TOKEN_A"


class TestCorruptedFile:
    def test_corrupted_json_recovers_to_empty(self, tmp_path):
        path = tmp_path / "tokens.json"
        path.write_text("not valid json!!!}", encoding="utf-8")
        store = DeltaStore("test-org", path=path)
        assert store._data == {}

    def test_empty_file_recovers_to_empty(self, tmp_path):
        path = tmp_path / "tokens.json"
        path.write_text("", encoding="utf-8")
        store = DeltaStore("test-org", path=path)
        assert store._data == {}

    def test_get_after_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "tokens.json"
        path.write_text("{bad json", encoding="utf-8")
        store = DeltaStore("test-org", path=path)
        assert store.get("mail:user@test.com") is None


class TestMultipleKeys:
    def test_multiple_keys_coexist(self, tmp_path):
        path = tmp_path / "tokens.json"
        store = DeltaStore("test-org", path=path)
        store.set("mail:user1@test.com", "MAIL_TOKEN", 10)
        store.set("teams:t1:c1", "TEAMS_TOKEN", 20)
        store.save()

        store2 = DeltaStore("test-org", path=path)
        assert store2.get("mail:user1@test.com")["delta_token"] == "MAIL_TOKEN"
        assert store2.get("teams:t1:c1")["delta_token"] == "TEAMS_TOKEN"

    def test_key_format_mail(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("mail:alice@corp.com", "T1", 1)
        assert store.get("mail:alice@corp.com") is not None

    def test_key_format_teams(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("teams:team-abc:channel-xyz", "T2", 2)
        assert store.get("teams:team-abc:channel-xyz") is not None

    def test_unknown_key_returns_none(self, tmp_path):
        store = DeltaStore("test-org", path=tmp_path / "tokens.json")
        store.set("mail:user@test.com", "TOKEN", 1)
        assert store.get("mail:other@test.com") is None
