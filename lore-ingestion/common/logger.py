"""
logger.py
─────────
Shared logging configuration for all Lore ingestion modules.

Console: colored, INFO level — human-readable during runs.
File:    full DEBUG log — for post-run analysis and debugging.

Usage:
    from common.logger import get_logger
    log = get_logger("gmail.fetcher")
    log.info("Fetching messages for %s", email)
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── ANSI color codes ──────────────────────────────────────────────────────────
_COLORS = {
    "DEBUG":    "\033[90m",      # gray
    "INFO":     "\033[92m",      # green
    "WARNING":  "\033[93m",      # yellow
    "ERROR":    "\033[91m",      # red
    "CRITICAL": "\033[95m",      # magenta
}
_RESET = "\033[0m"

# Track whether we've already initialized the root logger
_initialized = False
_file_handler = None


class _ColorFormatter(logging.Formatter):
    """Adds ANSI color to log level in console output."""

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, "")
        record.colored_level = f"{color}{record.levelname:5s}{_RESET}"
        return super().format(record)


def setup_logging(org: str = "", log_dir: str = "./output", source: str = "gmail") -> None:
    """
    Initialize root logging config. Call once at pipeline start.

    Creates:
      - Console handler (INFO, colored)
      - File handler (DEBUG, full detail) → {log_dir}/{org}_{source}_{date}.log
    """
    global _initialized, _file_handler
    if _initialized:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # ── Console handler ───────────────────────────────────────────────────
    # On Windows the default stdout encoding is cp1252 which can't render
    # Unicode box-drawing characters used in separator lines. Reconfigure
    # to UTF-8 so the console output matches the file handler.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass  # stdout replaced (pipe/redirect) — skip reconfigure
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console_fmt = _ColorFormatter(
        fmt="[%(asctime)s] [%(colored_level)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # ── File handler ──────────────────────────────────────────────────────
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = log_path / f"{org}_{source}_{date_str}.log" if org else log_path / f"{source}_{date_str}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)
    _file_handler = file_handler

    _initialized = True

    logger = logging.getLogger("common.logger")
    logger.debug("Logging initialized — console (INFO) + file (DEBUG): %s", log_file)


def get_logger(name: str) -> logging.Logger:
    """
    Get a named logger. Call setup_logging() first for file output.

    Usage:
        log = get_logger("gmail.fetcher")
        log.info("Processing user %s", email)
    """
    return logging.getLogger(name)
