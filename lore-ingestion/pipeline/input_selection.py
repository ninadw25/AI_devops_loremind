from __future__ import annotations

from glob import glob
from pathlib import Path
from typing import Iterable


_GLOB_CHARS = {"*", "?", "["}


def _has_glob(pattern: str) -> bool:
    return any(ch in pattern for ch in _GLOB_CHARS)


def _matching_paths(input_spec: str | Path) -> list[Path]:
    spec = str(input_spec)
    if _has_glob(spec):
        return [Path(match) for match in glob(spec)]
    return [Path(spec)]


def _slack_sibling_glob(path: Path) -> Path | None:
    name = path.name
    if "_slack_" not in name or not name.endswith(".jsonl"):
        return None

    prefix = name.split("_slack_", 1)[0]
    return path.parent / f"{prefix}_slack_*.jsonl"


def _sort_newest_first(paths: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve() if path.exists() else path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)

    return sorted(
        unique,
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )


def latest_nonempty_path(
    input_spec: str | Path,
    *,
    label: str = "input",
    logger=None,
) -> Path:
    """Resolve a path or glob to the newest non-empty file.

    If the newest file is empty, walk backward through older matches until a
    non-empty file is found. Raises FileNotFoundError if there are no matches
    or if all matching files are empty.
    """

    spec = str(input_spec)
    candidates = [path for path in _matching_paths(spec) if path.exists() and path.is_file()]
    if not candidates:
        raise FileNotFoundError(f"{label}: no files matched {spec!r}")

    newest_first = _sort_newest_first(candidates)
    newest = newest_first[0]

    if logger is not None:
        logger.info("%s newest file found: %s", label, newest)

    skipped_empty: list[Path] = []
    for candidate in newest_first:
        if candidate.stat().st_size > 0:
            if skipped_empty and logger is not None:
                for empty_path in skipped_empty:
                    logger.warning("%s skipped empty file: %s", label, empty_path)
                logger.info("%s selected fallback file: %s", label, candidate)
            elif logger is not None:
                logger.info("%s selected newest non-empty file: %s", label, candidate)

            if logger is not None:
                logger.info("%s final chosen input: %s", label, candidate)
            return candidate

        skipped_empty.append(candidate)

    if len(newest_first) == 1 and not _has_glob(spec):
        sibling_glob = _slack_sibling_glob(newest_first[0])
        if sibling_glob is not None:
            sibling_candidates = [
                path for path in _matching_paths(sibling_glob)
                if path.exists() and path.is_file() and path != newest_first[0]
            ]
            if sibling_candidates:
                if logger is not None:
                    logger.warning(
                        "%s explicit file is empty; searching siblings with %s",
                        label,
                        sibling_glob,
                    )
                return latest_nonempty_path(sibling_glob, label=label, logger=logger)

    if logger is not None:
        for empty_path in skipped_empty:
            logger.warning("%s skipped empty file: %s", label, empty_path)

    raise FileNotFoundError(f"{label}: all matching files are empty for {spec!r}")