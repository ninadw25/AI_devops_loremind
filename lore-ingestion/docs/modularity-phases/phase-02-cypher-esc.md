# Phase 2 — Centralize Cypher `_esc()`

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW
**Files touched:**
- `builders/cypher_utils.py` (NEW)
- `builders/unified_builder.py` (removed local `_esc`, imported from cypher_utils)
- `builders/post_processor.py` (removed local `_esc`, imported from cypher_utils)

## Changes

### New file: `builders/cypher_utils.py`

Defines `cypher_escape(s) -> str`. Single owner for Cypher literal escaping. Replacement order:

1. `\\` → `\\\\` (backslash first, so it doesn't double-escape later replacements)
2. `'` → `\\'`
3. `;` → `,` (semicolons split Cypher batch files; keeping a `;` inside a literal could fragment a batch)
4. `\n` → space
5. `\r` → removed

### Removed duplications

| File | Was | Now |
|------|-----|-----|
| `builders/unified_builder.py:37-40` | local `def _esc` with `;`→`,` | `from builders.cypher_utils import cypher_escape as _esc` |
| `builders/post_processor.py:38-41` | local `def _esc` **without** `;`→`,` | same import as above |

The `as _esc` alias preserves all ~20 existing call sites unchanged.

## Bug fixed (silent, latent)

`builders/post_processor.py` previously did not strip `;` from Cypher literals. If a topic name or process description contained a semicolon, the emitted Cypher would split at that point and the rest of the literal would be interpreted as a new statement → either a Cypher syntax error or, worse, an accidentally-executed fragment. Now uniformly stripped.

## Verification

```bash
$ grep -rn "def _esc\b\|def cypher_escape\b" --include="*.py" .
builders/cypher_utils.py:15:def cypher_escape(s) -> str:

$ python -c "from builders.cypher_utils import cypher_escape; print(repr(cypher_escape(\"a'b;c\nd\\\\e\")))"
"a\\'b,c d\\\\e"

$ python -c "import builders.unified_builder; import builders.post_processor; print('imports OK')"
imports OK
```

- Only one `_esc`/`cypher_escape` definition repo-wide.
- Escape function behaves as expected (round-trip example: `a'b;c\nd\\e` → `a\'b,c d\\e`).
- Both downstream modules import cleanly.

## Rollback

```bash
git checkout HEAD -- builders/unified_builder.py builders/post_processor.py
rm builders/cypher_utils.py
```

## Next phase

**Step 3 — Move node-ID formula into `common/hashing.py`**

Two callers currently mint node IDs inline with the format `f"{type}:{short_hash(...)}"`:

- `pipeline/validator.py:697-709` — 7 prefixes (`person`, `org`, `topic`, `dec`, `task`, `fact`, `evt`) keyed by `name.lower()` or `thread_id + name`.
- `builders/post_processor.py:281, 340, 357, 368` — `topic`, `proc`, `task` prefixes.

Action: add `common/hashing.py:node_id_for(node_type, name, scope=None)` plus a `HASH_VERSION = "v1"` constant. Replace inline calls. Verify by generating 100 IDs both before and after — must match byte-for-byte.

Risk note: `name.lower()` vs raw `name` differs between callers. Read both sites carefully before extracting; preserve each caller's normalization.
