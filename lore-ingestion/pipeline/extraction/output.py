"""
pipeline/extraction/output.py
─────────────────────────────
Output writing helper for the LLM extraction stage.
"""

from __future__ import annotations

import json
from pathlib import Path


def _save_output(path: Path, results: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
