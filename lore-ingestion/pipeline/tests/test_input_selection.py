from __future__ import annotations

import os
from pathlib import Path

import pytest

from pipeline.input_selection import latest_nonempty_path


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, tuple]] = []

    def info(self, msg: str, *args):
        self.messages.append(("info", msg, args))

    def warning(self, msg: str, *args):
        self.messages.append(("warning", msg, args))


def _write_with_mtime(path: Path, content: str, mtime: int) -> Path:
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_latest_nonempty_prefers_newest_nonempty(tmp_path: Path):
    older = _write_with_mtime(tmp_path / "ezobooks_slack_1.jsonl", "old\n", 100)
    newer = _write_with_mtime(tmp_path / "ezobooks_slack_2.jsonl", "new\n", 200)

    selected = latest_nonempty_path(str(tmp_path / "ezobooks_slack_*.jsonl"))

    assert selected == newer
    assert selected.read_text(encoding="utf-8") == "new\n"


def test_latest_nonempty_skips_newest_empty_file(tmp_path: Path):
    older = _write_with_mtime(tmp_path / "ezobooks_slack_1.jsonl", "old\n", 100)
    _write_with_mtime(tmp_path / "ezobooks_slack_2.jsonl", "", 200)
    logger = DummyLogger()

    selected = latest_nonempty_path(
        str(tmp_path / "ezobooks_slack_*.jsonl"),
        logger=logger,
        label="Slack raw input",
    )

    assert selected == older
    assert any("newest file found" in msg for level, msg, _ in logger.messages if level == "info")
    assert any("skipped empty file" in msg for level, msg, _ in logger.messages if level == "warning")
    assert any("selected fallback file" in msg for level, msg, _ in logger.messages if level == "info")
    assert any("final chosen input" in msg for level, msg, _ in logger.messages if level == "info")


def test_latest_nonempty_skips_multiple_empty_files(tmp_path: Path):
    oldest = _write_with_mtime(tmp_path / "ezobooks_slack_1.jsonl", "oldest\n", 100)
    _write_with_mtime(tmp_path / "ezobooks_slack_2.jsonl", "", 200)
    _write_with_mtime(tmp_path / "ezobooks_slack_3.jsonl", "", 300)

    selected = latest_nonempty_path(str(tmp_path / "ezobooks_slack_*.jsonl"))

    assert selected == oldest


def test_latest_nonempty_raises_when_all_empty(tmp_path: Path):
    _write_with_mtime(tmp_path / "ezobooks_slack_1.jsonl", "", 100)
    _write_with_mtime(tmp_path / "ezobooks_slack_2.jsonl", "", 200)

    with pytest.raises(FileNotFoundError, match="all matching files are empty"):
        latest_nonempty_path(str(tmp_path / "ezobooks_slack_*.jsonl"))


def test_latest_nonempty_accepts_direct_file_path(tmp_path: Path):
    path = _write_with_mtime(tmp_path / "ezobooks_slack_1.jsonl", "hello\n", 100)

    selected = latest_nonempty_path(path)

    assert selected == path


def test_latest_nonempty_explicit_empty_slack_path_falls_back_to_siblings(tmp_path: Path):
    empty_new = _write_with_mtime(tmp_path / "ezobooks_slack_2026-05-28.jsonl", "", 300)
    older = _write_with_mtime(tmp_path / "ezobooks_slack_2026-05-27.jsonl", "old\n", 200)
    logger = DummyLogger()

    selected = latest_nonempty_path(empty_new, logger=logger, label="Slack raw input")

    assert selected == older
    assert any("explicit file is empty" in msg for level, msg, _ in logger.messages if level == "warning")