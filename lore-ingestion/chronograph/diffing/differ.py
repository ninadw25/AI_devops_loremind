"""
Semantic differ — compare symbols between two versions of a file.

Simplified GumTree-inspired approach:
  1. Match symbols by qualified_name
  2. Compare body_hash to detect modifications
  3. Detect renames by similar body_hash + different name
  4. Report added/removed symbols
"""

import logging
from difflib import SequenceMatcher
from typing import Optional

from chronograph.entities import Symbol, DiffRecord

log = logging.getLogger("chronograph.differ")

# Threshold for body similarity when detecting renames
RENAME_SIMILARITY_THRESHOLD = 0.7


def diff_symbols(
    old_symbols: list[Symbol],
    new_symbols: list[Symbol],
    snapshot_id: str,
    file_path: str,
) -> list[DiffRecord]:
    """
    Compare two lists of symbols and produce DiffRecords.

    Returns list of changes: added, removed, modified, renamed.
    """
    old_by_name: dict[str, Symbol] = {s.qualified_name: s for s in old_symbols if s.kind != "module"}
    new_by_name: dict[str, Symbol] = {s.qualified_name: s for s in new_symbols if s.kind != "module"}

    diffs: list[DiffRecord] = []
    matched_old: set[str] = set()
    matched_new: set[str] = set()

    # Phase 1: Match by qualified_name
    for qname, new_sym in new_by_name.items():
        if qname in old_by_name:
            old_sym = old_by_name[qname]
            matched_old.add(qname)
            matched_new.add(qname)

            if old_sym.body_hash != new_sym.body_hash:
                # Something changed — classify it
                change_type = _classify_change(old_sym, new_sym)
                diffs.append(DiffRecord(
                    entity_id=f"entity:{file_path}:{qname}",
                    snapshot_id=snapshot_id,
                    change_type=change_type,
                    details={
                        "old_lines": f"{old_sym.start_line}-{old_sym.end_line}",
                        "new_lines": f"{new_sym.start_line}-{new_sym.end_line}",
                        "old_hash": old_sym.body_hash[:12],
                        "new_hash": new_sym.body_hash[:12],
                    },
                ))

    # Phase 2: Detect renames among unmatched symbols
    unmatched_old = [old_by_name[n] for n in old_by_name if n not in matched_old]
    unmatched_new = [new_by_name[n] for n in new_by_name if n not in matched_new]

    rename_pairs = _detect_renames(unmatched_old, unmatched_new)
    for old_sym, new_sym in rename_pairs:
        matched_old.add(old_sym.qualified_name)
        matched_new.add(new_sym.qualified_name)
        diffs.append(DiffRecord(
            entity_id=f"entity:{file_path}:{new_sym.qualified_name}",
            snapshot_id=snapshot_id,
            change_type="renamed",
            details={
                "old_name": old_sym.qualified_name,
                "new_name": new_sym.qualified_name,
                "body_similarity": _body_similarity(old_sym, new_sym),
            },
        ))

    # Phase 3: Remaining unmatched = added/removed
    for qname in old_by_name:
        if qname not in matched_old:
            sym = old_by_name[qname]
            diffs.append(DiffRecord(
                entity_id=f"entity:{file_path}:{qname}",
                snapshot_id=snapshot_id,
                change_type="removed",
                details={
                    "kind": sym.kind,
                    "lines": f"{sym.start_line}-{sym.end_line}",
                },
            ))

    for qname in new_by_name:
        if qname not in matched_new:
            sym = new_by_name[qname]
            diffs.append(DiffRecord(
                entity_id=f"entity:{file_path}:{qname}",
                snapshot_id=snapshot_id,
                change_type="added",
                details={
                    "kind": sym.kind,
                    "lines": f"{sym.start_line}-{sym.end_line}",
                },
            ))

    return diffs


def _classify_change(old_sym: Symbol, new_sym: Symbol) -> str:
    """Classify what kind of modification occurred."""
    # Signature changed (params different)?
    if old_sym.signature != new_sym.signature:
        return "signature_change"

    # Only docstring changed? (simple heuristic: body changed but first meaningful line same)
    old_lines = [l.strip() for l in old_sym.body_text.splitlines() if l.strip()]
    new_lines = [l.strip() for l in new_sym.body_text.splitlines() if l.strip()]

    if old_lines and new_lines:
        # Check if only triple-quoted strings changed
        old_no_doc = _strip_docstring(old_sym.body_text)
        new_no_doc = _strip_docstring(new_sym.body_text)
        if old_no_doc == new_no_doc:
            return "docstring_change"

    return "body_change"


def _strip_docstring(body: str) -> str:
    """Remove docstrings (triple-quoted) from body for comparison."""
    import re
    return re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', '', body).strip()


def _detect_renames(
    old_unmatched: list[Symbol],
    new_unmatched: list[Symbol],
) -> list[tuple[Symbol, Symbol]]:
    """Detect renamed symbols by comparing body similarity."""
    pairs = []
    used_new: set[str] = set()

    for old_sym in old_unmatched:
        if old_sym.kind == "module":
            continue
        best_match: Optional[Symbol] = None
        best_score = 0.0

        for new_sym in new_unmatched:
            if new_sym.qualified_name in used_new:
                continue
            if new_sym.kind != old_sym.kind:
                continue

            score = _body_similarity(old_sym, new_sym)
            if score > RENAME_SIMILARITY_THRESHOLD and score > best_score:
                best_score = score
                best_match = new_sym

        if best_match:
            pairs.append((old_sym, best_match))
            used_new.add(best_match.qualified_name)

    return pairs


def _body_similarity(a: Symbol, b: Symbol) -> float:
    """Compute similarity ratio between two symbol bodies."""
    if not a.body_text or not b.body_text:
        return 0.0
    return SequenceMatcher(None, a.body_text, b.body_text).ratio()
