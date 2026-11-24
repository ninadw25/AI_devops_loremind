# Knowledge Distillation Pipeline — Design Spec

**Date:** 2026-04-14
**Author:** Generated via Claude Code brainstorming session
**Status:** Draft — awaiting user review

## Purpose

Build a resumable, multi-teacher knowledge-distillation data-generation pipeline. Three teacher LLMs each label a distinct subset of Gmail threads with graph-triple extractions. The combined `labeled.jsonl` files across teachers form the training dataset used to fine-tune an open-source student model.

## Goals & Non-Goals

**Goals**
- Produce ~8,000 distinct labeled threads across 3 teachers (no overlap).
- Support 3 independent terminal runs (one per teacher), each with its own checkpoint, rate limit, and output file.
- Resume from any interruption without losing completed work.
- Preserve `thread_id` on every output record — it links back to source emails downstream.
- Real-time output persistence (no end-of-run batch write).
- Visible progress: per-thread latency, tokens, ETA, rate.

**Non-Goals**
- The fine-tuning step itself (separate project).
- Teacher ensemble consensus / voting — each thread is labeled by exactly one teacher.
- Web UI, dashboards, metrics server.
- Automatic cross-day scheduling for the OpenRouter daily-reset (user will restart manually post-midnight).

## Teacher Models

All teachers use **thinking disabled** to produce directly-usable training labels. All teachers share the existing system prompt (`pipeline/triple_extractor.py::SYSTEM_PROMPT`), existing thread prompt builder (`build_thread_prompt`), and existing validator (`pipeline/validator.py::validate_thread`).

| Teacher | Model ID | Endpoint | API Key Env | RPM Cap | Hard Daily Cap | Allocation |
|---------|----------|----------|-------------|---------|----------------|------------|
| nemotron | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | `OPENROUTER_API_KEY` | 50 | 1000 req/day | 2000 threads |
| kimi | `moonshotai/kimi-k2-instruct` | NVIDIA | `NVIDIA_API_KEY` (fallback `NVIDIA_API_KEY1`) | 40 | — | 3000 threads |
| qwen | `qwen/qwen3-next-80b-a3b-instruct` | NVIDIA | `NVIDIA_API_KEY` (fallback `NVIDIA_API_KEY1`) | 40 | — | 3000 threads |

**Total labeled dataset target:** 8000 threads.

**Reusable call parameters per model** — each teacher's config file owns its `build_call_params()`, which passes `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` when the model supports the flag, and omits it otherwise.

## Data Source

- Input: `output/filtered/ezobooks_gmail_filtered.jsonl` (~32,300 threads post-filter).
- Reference registry: `output/knowledge/ezobooks_person_registry.json` (for validator name resolution).
- Thread loader and prompt builder: reused from `pipeline/triple_extractor.py`.

## File Layout

```
knowledge_distillation/
├── __init__.py
├── allocator.py                     # Stratified allocation (one-time, cached)
├── distill.py                       # Main runner (picks teacher via --teacher flag)
├── assignments.json                 # Generated: {thread_id: "nemotron"|"kimi"|"qwen"}
├── models/
│   ├── __init__.py
│   ├── base.py                      # TeacherBase abstract class
│   ├── nemotron_openrouter.py       # OpenRouter client, nemotron config
│   ├── kimi_nvidia.py               # NVIDIA client, kimi config
│   └── qwen_nvidia.py               # NVIDIA client, qwen config
└── output/
    ├── nemotron/
    │   ├── labeled.jsonl            # Append-only, one thread per line
    │   ├── checkpoint.json          # {completed_ids, failed_ids, stats, last_updated}
    │   ├── failed.jsonl             # Permanent failures with error context
    │   └── progress.log             # Timestamped log lines mirrored from terminal
    ├── kimi/    … same structure …
    └── qwen/    … same structure …
```

## Stratified Allocator

**Purpose:** guarantee each teacher sees a balanced distribution of thread complexity so the student model learns generalizable patterns, not per-teacher biases.

**Algorithm:**
1. Load 32,300 filtered threads, bucket by email count:
   - `tiny`: 1 email
   - `small`: 2–6 emails
   - `medium`: 7–15 emails
   - `large`: 16+ emails
2. Within each bucket, shuffle with a fixed seed (42) for reproducibility.
3. Target allocation ratio: `nemotron:kimi:qwen = 2:3:3`. For each bucket, assign threads round-robin in that ratio until each teacher hits its total (2000 / 3000 / 3000). If a bucket is exhausted before the quota is met, the allocator pulls from the next bucket.
4. Emit `assignments.json`:
   ```json
   {
     "generated_at": "2026-04-14T20:00:00",
     "seed": 42,
     "bucket_counts": {"tiny": 28000, "small": 3500, "medium": 600, "large": 200},
     "allocations": {
       "19af1a9b52f97280": "nemotron",
       "199f12347f147c01": "kimi",
       ...
     },
     "totals": {"nemotron": 2000, "kimi": 3000, "qwen": 3000}
   }
   ```

**Idempotency:** if `assignments.json` exists, `allocator.py` exits without regenerating (unless `--reallocate` is passed). This prevents accidental reassignment mid-run.

## Main Runner (distill.py)

### CLI

```bash
# Core flags
--teacher {nemotron|kimi|qwen}   # required
--batch-size N                   # hard stop after N completed threads (optional)
--resume                         # continue from checkpoint (default behavior if state exists)
--retry-failed                   # rerun only threads in failed.jsonl
--concurrency N                  # override default (default: 5)
--rpm N                          # override model's default RPM cap
```

### Execution Flow

1. Parse CLI; load `assignments.json`; filter to `{tid: t for tid, t in allocations.items() if t == args.teacher}`.
2. Load `output/{teacher}/checkpoint.json` if it exists. Read `completed_ids` and `failed_ids`.
3. **Crash-safety scan:** read `labeled.jsonl` line-by-line; any `thread_id` found there but not in `completed_ids` gets added to the in-memory set (covers the case where the process crashed between labeled-line write and checkpoint update).
4. Compute `todo = assignments[teacher] - completed - failed`.
5. If `--batch-size N`, cap `todo` to the first N items.
6. Instantiate `TeacherBase` subclass for the chosen teacher. Create:
   - `AsyncOpenAI` client (OpenRouter or NVIDIA base_url depending on teacher)
   - `AsyncRateLimiter(max_calls=rpm_cap, period=60.0)`
   - `asyncio.Semaphore(concurrency)`
7. Dispatch `run_thread()` coroutines; stream results to:
   - `labeled.jsonl` (append + flush per thread)
   - terminal (single line per thread + summary every 10)
8. Every 10 completed threads: rewrite `checkpoint.json` atomically (write to `.tmp`, rename).
9. On `SIGINT` (Ctrl-C): drain in-flight requests, flush checkpoint, exit cleanly with exit code 130.

### Per-Thread Output Record

```json
{
  "thread_id": "19af1a9b52f97280",
  "teacher": "kimi",
  "model": "moonshotai/kimi-k2-instruct",
  "email_count": 2,
  "triples": [
    {"subject": {...}, "predicate": "DECIDED", "object": {...}, "evidence_sentence": "..."}
  ],
  "thread_summary": "Abhijit requested leave for ...",
  "department_tags": ["hr"],
  "sensitivity": "internal",
  "validation": {"approved": 5, "rejected": 0, "auto_corrected": 0, "rejected_reasons": []},
  "timing": {
    "started_at": "2026-04-14T20:00:00.123Z",
    "elapsed_s": 4.2
  },
  "tokens": {"prompt": 4500, "completion": 800, "reasoning": 0, "total": 5300}
}
```

`thread_id` is deliberately the first key so it's visible at a glance when tailing the file.

### Per-Thread Terminal Line

```
[0234/2000] ✓ 19af1a9b52f97280  2e  4.2s  in=4500 out=800  triples=5 valid=5  rate: 22.4/min  ETA: 1h 22m
```

Every 10 threads, a summary line with cumulative wall time, total tokens, validator pass rate, and revised ETA.

### Checkpoint Format

```json
{
  "teacher": "kimi",
  "last_updated": "2026-04-14T20:15:23Z",
  "threads_total": 3000,
  "threads_done": 234,
  "threads_failed": 2,
  "tokens_total": 1_245_000,
  "elapsed_s": 632.1,
  "completed_ids": ["19af1a9b52f97280", "199f12347f147c01", ...],
  "failed_ids": ["19abc...", "195cc..."]
}
```

Written atomically (temp file + rename).

## Failure Handling

| Failure Mode | Handler |
|--------------|---------|
| `asyncio.TimeoutError`, connection reset | 3 retries with exponential backoff (2s, 4s, 8s) |
| HTTP 429 (rate limit) | Sleep 60s, retry. Does NOT count against the 3 retries |
| HTTP 401/403 on NVIDIA endpoint | One-time fallback to `NVIDIA_API_KEY1` env var; if that also fails, the thread is treated as a permanent failure |
| HTTP 5xx | Retry within the 3-retry budget |
| JSON parse error | Retry within budget (the existing triple_extractor logic already appends error message to the conversation for self-correction) |
| Pydantic `ValidationError` after retries | Log to `failed.jsonl` with error detail, move on |
| Unknown exception | Log to `failed.jsonl` with stack trace, move on |

`failed.jsonl` entry format:
```json
{
  "thread_id": "19abc...",
  "teacher": "kimi",
  "error_type": "ValidationError",
  "error_message": "triples.0.subject.type: Field required",
  "attempts": 3,
  "failed_at": "2026-04-14T20:05:12Z"
}
```

`--retry-failed` reads `failed.jsonl`, treats all listed IDs as `todo` (removing from `failed_ids` in checkpoint), and retries each.

## Rate Limiting

Reuses `pipeline/triple_extractor.py::AsyncRateLimiter` (sliding-window). Per-teacher defaults:

| Teacher | RPM cap (configured) | NVIDIA/OR max | Margin |
|---------|----------------------|---------------|--------|
| nemotron | 45 | 50 | –5 RPM buffer for safety |
| kimi | 38 | 40 | –2 RPM buffer |
| qwen | 38 | 40 | –2 RPM buffer |

**Daily cap tracking** for nemotron: after 1000 successful completions in a 24h window, the runner prints a clear message and exits cleanly. User restarts after midnight UTC reset.

## Reused Components

No duplication — the following are imported from the existing pipeline:

- `pipeline.triple_extractor.SYSTEM_PROMPT`
- `pipeline.triple_extractor.AsyncRateLimiter`
- `pipeline.triple_extractor.group_by_thread`
- `pipeline.triple_extractor.build_thread_prompt`
- `pipeline.triple_extractor.build_org_context`
- `pipeline.validator.build_name_lookup`
- `pipeline.validator.validate_thread`
- `common.schemas_extraction.ExtractionResult`

The distillation pipeline is a **consumer** of the existing extractor's primitives, not a rewrite.

## Testing / Verification Plan

No formal test suite (consistent with project convention). Manual validation:

1. Run allocator; inspect `assignments.json` — verify counts (2000/3000/3000) and bucket representation per teacher.
2. Run each teacher with `--batch-size 5` first; inspect `labeled.jsonl` for correct schema and `thread_id` preservation.
3. Kill one run mid-way (Ctrl-C); rerun with `--resume`; verify no duplicates in `labeled.jsonl` and correct restart index.
4. Let one model run hit its daily cap (nemotron); verify graceful exit message.
5. Spot-check 10 threads across teachers — do extractions look sensible and schema-valid?

## Terminal Commands (User-Facing)

```bash
cd /home/zeiko/work/loremind/lore/lore-ingestion

# One-time: generate stratified assignments
../.venv/bin/python -m knowledge_distillation.allocator

# Three terminals, one per teacher, starting with a 100-thread batch each:
# Terminal 1
../.venv/bin/python -m knowledge_distillation.distill --teacher nemotron --batch-size 100
# Terminal 2
../.venv/bin/python -m knowledge_distillation.distill --teacher kimi --batch-size 100
# Terminal 3
../.venv/bin/python -m knowledge_distillation.distill --teacher qwen --batch-size 100

# Resume (no batch-size = run to completion of this teacher's allocation)
../.venv/bin/python -m knowledge_distillation.distill --teacher nemotron --resume

# Retry failures after all three teachers complete
../.venv/bin/python -m knowledge_distillation.distill --teacher nemotron --retry-failed
```

## Open Questions / Future Work

- **Nemotron daily cap detection:** we infer the cap via 24h request count inside the process. If multiple terminals are launched on the same day for nemotron, the second one won't know about the first's consumption. The fix is to track the cap in `checkpoint.json` with a rolling 24h window. Deferred to implementation.
- **Final dataset merge script:** once all three teachers complete, a simple `merge_labeled.py` can concatenate the three `labeled.jsonl` files into one 8000-line training file. Left out of this spec; add when the user is ready to fine-tune.
