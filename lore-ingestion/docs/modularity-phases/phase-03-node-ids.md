# Phase 3 — Move node-ID formula into `common/hashing.py`

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (byte-for-byte parity verified before swap)
**Files touched:**
- `common/hashing.py` — added `node_id_for()` and `HASH_VERSION = "v1"`
- `pipeline/validator.py` — `_assign_node_ids()` now delegates to `node_id_for`
- `builders/post_processor.py` — 4 inline ID-mintings now call `node_id_for`

## Two patterns identified across all 9 inline call sites

| Pattern | Types | Formula |
|---------|-------|---------|
| Name-keyed (lowercased) | Person, Organization, Topic, Process | `"{prefix}:{short_hash(name.lower())}"` |
| Scoped (no lowercase) | Decision, Task, Fact, Event | `"{prefix}:{short_hash(scope + name)}"` |

Prefix mapping: `Person→person`, `Organization→org`, `Topic→topic`, `Process→proc`, `Decision→dec`, `Task→task`, `Fact→fact`, `Event→evt`.

## New API

```python
from common.hashing import node_id_for, HASH_VERSION

node_id_for("Person", "Aarav Sharma")
# → "person:0fedaf4e91cd"   (lowercased internally)

node_id_for("Decision", "Approve budget", scope="thread-003")
# → "dec:b303254ee686"      (hash of "thread-003Approve budget")

node_id_for("Decision", "X")        # ValueError: scope required
node_id_for("Bogus", "X")           # ValueError: unknown type
```

`HASH_VERSION` is bumped only when the formula changes (currently `"v1"`).

## Parity test (run before swap, all 14 samples OK)

```
validator  Person        'Aarav Sharma'      -> person:0fedaf4e91cd  OK
validator  Person        'AARAV sharma'      -> person:0fedaf4e91cd  OK   (case-insensitive)
validator  Organization  'Acme; Inc.'        -> org:d04c175076c3     OK
validator  Topic         'GST refund'        -> topic:f58451956de3   OK
validator  Decision      'Approve budget'    -> dec:b303254ee686     OK
validator  Task          'Send invoice'      -> task:a99005de51ce    OK
validator  Fact          'Revenue up 12%'    -> fact:ac97693d0300    OK
validator  Event         'All-hands meeting' -> evt:5fab1158856c     OK
pp.topic   'GST refund'                      -> topic:f58451956de3   OK
pp.process 'GST refund'                      -> proc:f58451956de3    OK
pp.task    ('GST refund','Reconcile…')       -> task:9d8ca9459676    OK
```

## Pre-existing inconsistency surfaced (not fixed in this phase)

`builders/post_processor.py` mints **Process-step Task IDs** using `node_id_for("Task", task_desc, scope=topic)` — i.e. scoped by topic name, not by thread.

The Validator (Stage 6) mints **thread Task IDs** using `node_id_for("Task", name, scope=thread_id)` — scoped by thread.

These produce **disjoint ID spaces.** The post-processor's `MATCH (tk:Task {task_id: …})` at line 366 will never match any task that the validator emitted; it can only match tasks the post-processor itself created in the same run. This is pre-existing behavior preserved exactly. Flagged here for a future fix — likely either the post-processor's `HAS_STEP` edges are silently no-ops in Neo4j today, or there is a separate task-creation pass we haven't audited. Worth a follow-up: query the live graph for `MATCH ()-[:HAS_STEP]->() RETURN count(*)` to find out which.

## Verification

```bash
$ grep -rn "f\"person:\|f\"org:\|f\"topic:\|f\"dec:\|f\"task:\|f\"fact:\|f\"evt:\|f\"proc:" --include="*.py" .
# (empty — no inline ID minting remains)

$ python -c "import pipeline.validator; import builders.post_processor; import builders.unified_builder; print('all imports OK')"
all imports OK
```

## Rollback

```bash
git checkout HEAD -- common/hashing.py pipeline/validator.py builders/post_processor.py
```

## Next phase

**Step 4 — Add `common/config.py` Pydantic Settings module**

Scope: introduce a single `Settings(BaseSettings)` class that owns every env var the pipeline reads. Initially additive — do NOT remove existing `os.getenv` calls. Provide a `settings.validate_for(stage_name)` helper that fails fast at process start if required vars for that stage are missing.

Affected env vars seen during audit:
- LLM: `NVIDIA_API_KEY`, `NVIDIA_BASE_URL`, `EXTRACTION_MODEL`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `ANTHROPIC_API_KEY`, `LOCAL_MODEL_URL`.
- Neo4j: `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.
- Mongo: `MONGODB_URI`, `MONGODB_DB_NAME`, `CHRONOGRAPH_DB_NAME`.
- GitLab/GitHub: `GITLAB_PAT`, `GITLAB_BASE_URL`, `GITHUB_PAT`.
- Chronograph tuning: `OPENROUTER_EMBED_MODEL`, `OPENROUTER_LLM_MODEL`, `EMBED_BATCH_SIZE`, `EMBED_DELAY_S`, `OPENROUTER_TIMEOUT_S`, `CHRONOGRAPH_BACKFILL_DAYS`, `CHRONOGRAPH_MAX_FILE_SIZE`.
- Misc: `ORG`, `OUTPUT_DIR`.

Question for next session: Pydantic v1 or v2 is fine — but we should pick consistent with whatever `lore-backend` uses. Check `lore-backend/core/settings` before writing the module.
