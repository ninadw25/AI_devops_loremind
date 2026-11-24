"""
knowledge_distillation/distill.py
───────────────────────────────────
Main runner for knowledge distillation.

Reads teacher assignment from assignments.json, resumes from
checkpoint, calls the teacher LLM for each assigned thread,
validates output, writes labeled.jsonl in real-time.

Usage:
    # First-time: run 100 threads for each teacher
    python -m knowledge_distillation.distill --teacher nemotron --batch-size 100
    python -m knowledge_distillation.distill --teacher kimi    --batch-size 100
    python -m knowledge_distillation.distill --teacher qwen    --batch-size 100

    # Resume (picks up where checkpoint left off)
    python -m knowledge_distillation.distill --teacher nemotron

    # Retry permanent failures
    python -m knowledge_distillation.distill --teacher kimi --retry-failed
"""

import argparse
import asyncio
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI, AuthenticationError, RateLimitError
from pydantic import ValidationError

from pipeline.triple_extractor import (
    AsyncRateLimiter,
    SYSTEM_PROMPT,
    group_by_thread,
    build_thread_prompt,
    build_org_context,
    load_person_registry,
)
from pipeline.validator import validate_thread, build_name_lookup
from common.schemas_extraction import ExtractionResult

from knowledge_distillation.models.nemotron_openrouter import NemotronTeacher
from knowledge_distillation.models.kimi_nvidia import KimiTeacher
from knowledge_distillation.models.qwen_nvidia import QwenTeacher
from knowledge_distillation.models.minimax_nvidia import MinimaxTeacher
from knowledge_distillation.models.nemotron_super_nvidia import NemotronSuperTeacher
from knowledge_distillation.models.glm_nvidia import GLMTeacher
from knowledge_distillation.models.ns3_0_nvidia import NS3_0Teacher
from knowledge_distillation.models.ns3_1_nvidia import NS3_1Teacher
from knowledge_distillation.models.ns3_2_nvidia import NS3_2Teacher

# ── Paths ─────────────────────────────────────────────────────────────

FILTERED_PATH = Path("output/filtered/ezobooks_gmail_filtered.jsonl")
from common.output_paths import KnowledgePaths as _KP
REGISTRY_PATH = _KP(org="ezobooks").person_registry
ASSIGNMENTS_PATH = Path("knowledge_distillation/assignments.json")
OUTPUT_BASE = Path("knowledge_distillation/output")

TEACHERS = {
    # Dataset 1
    "nemotron":   NemotronTeacher(),
    "kimi":       KimiTeacher(),
    "qwen":       QwenTeacher(),
    # Dataset 2
    "minimax":    MinimaxTeacher(),
    "nemotron_s": NemotronSuperTeacher(),
    "glm":        GLMTeacher(),
    # Dataset 3 — Nemotron-Super sharded across 3 API keys
    "ns3_0":      NS3_0Teacher(),
    "ns3_1":      NS3_1Teacher(),
    "ns3_2":      NS3_2Teacher(),
}

# ── Constants ─────────────────────────────────────────────────────────

MAX_RETRIES = 3
RETRY_BACKOFF = [2, 4, 8]
CHECKPOINT_EVERY = 10   # save checkpoint every N completed threads
SUMMARY_EVERY = 10      # print summary block every N threads
RATE_LIMIT_SLEEP = 30   # seconds to sleep on HTTP 429


# ══════════════════════════════════════════════════════════════════════
# CHECKPOINT MANAGEMENT
# ══════════════════════════════════════════════════════════════════════

def _output_dir(teacher: str) -> Path:
    d = OUTPUT_BASE / teacher
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_checkpoint(teacher: str) -> dict:
    """Load checkpoint.json, or return a fresh empty state."""
    path = _output_dir(teacher) / "checkpoint.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Ensure all required fields exist (forward-compat)
            data.setdefault("completed_ids", [])
            data.setdefault("failed_ids", [])
            data.setdefault("tokens_total", 0)
            data.setdefault("elapsed_s", 0.0)
            data.setdefault("daily_date", "")
            data.setdefault("daily_count", 0)
            return data
        except (json.JSONDecodeError, OSError):
            print(f"[WARN] Corrupt checkpoint at {path}, starting fresh.")
    return {
        "teacher": teacher,
        "last_updated": "",
        "threads_total": 0,
        "threads_done": 0,
        "threads_failed": 0,
        "tokens_total": 0,
        "elapsed_s": 0.0,
        "completed_ids": [],
        "failed_ids": [],
        "daily_date": "",
        "daily_count": 0,
    }


def save_checkpoint(teacher: str, state: dict) -> None:
    """Write checkpoint.json atomically (temp file + rename)."""
    path = _output_dir(teacher) / "checkpoint.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.rename(path)


def scan_labeled_for_crash_recovery(teacher: str) -> set[str]:
    """Re-scan labeled.jsonl for thread_ids not in checkpoint.

    Covers the case where a thread was written to labeled.jsonl but
    the process crashed before the next checkpoint flush.
    """
    path = _output_dir(teacher) / "labeled.jsonl"
    found: set[str] = set()
    if not path.exists():
        return found
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                tid = record.get("thread_id", "")
                if tid:
                    found.add(tid)
            except json.JSONDecodeError:
                pass
    return found


# ══════════════════════════════════════════════════════════════════════
# DAILY CAP TRACKING (Nemotron only)
# ══════════════════════════════════════════════════════════════════════

def get_today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_and_update_daily_cap(
    teacher_obj, state: dict, increment: bool = False
) -> bool:
    """Check if daily cap is reached. Optionally increment count.

    Returns True if cap IS reached (caller should stop).
    Returns False if we're still under the cap.
    """
    if teacher_obj.daily_cap is None:
        return False

    today = get_today_utc()
    if state.get("daily_date") != today:
        # New day — reset counter
        state["daily_date"] = today
        state["daily_count"] = 0

    if state["daily_count"] >= teacher_obj.daily_cap:
        return True  # cap reached

    if increment:
        state["daily_count"] += 1

    return False


# ══════════════════════════════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════════════════════════════

def _fmt_eta(remaining: int, rate_per_min: float) -> str:
    if rate_per_min <= 0 or remaining <= 0:
        return "?"
    minutes = remaining / rate_per_min
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══════════════════════════════════════════════════════════════════════
# PER-THREAD API CALL (with retries + fallback)
# ══════════════════════════════════════════════════════════════════════

async def _api_call_with_429_retry(
    client: AsyncOpenAI,
    messages: list[dict],
    call_params: dict,
) -> object:
    """Call the API, sleeping 60s and retrying indefinitely on 429.

    All other exceptions propagate to the caller.
    """
    while True:
        try:
            return await client.chat.completions.create(
                messages=messages, **call_params
            )
        except RateLimitError:
            print(
                f"  [429] Rate limit hit — sleeping {RATE_LIMIT_SLEEP}s ...",
                flush=True,
            )
            await asyncio.sleep(RATE_LIMIT_SLEEP)


async def run_thread(
    tid: str,
    thread: dict,
    teacher_obj,
    primary_client: AsyncOpenAI,
    fallback_client: AsyncOpenAI | None,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    name_lookup: dict,
    persons_by_id: dict,
    email_to_person: dict,
    org_context: str,
    request_gate: asyncio.Lock,
    last_fire_time: list[float],
    min_request_interval: float,
) -> dict:
    """Extract and validate triples for a single thread.

    Returns a result dict with keys:
      success   : bool
      thread_id : str
      record    : dict (if success=True)  — the full labeled record
      validated : dict (if success=True)  — validator output
      rejected  : list (if success=True)  — rejected triples
      error_type: str (if success=False)
      error_msg : str (if success=False)
      attempts  : int
      tokens    : dict
      elapsed_s : float
      started_at: str
    """
    started_at = _now_iso()

    user_prompt = build_thread_prompt(
        thread, email_to_person, persons_by_id, org_context
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]
    call_params = teacher_obj.build_call_params()
    email_count = thread.get("email_count", len(thread.get("emails", [])))

    client = primary_client
    used_fallback = False
    last_response_text = ""
    attempt = 0

    async with semaphore:
        # Start timing only after acquiring the semaphore slot
        # (excludes queue wait time; shows actual API call duration)
        t0 = time.monotonic()
        while attempt < MAX_RETRIES:
            try:
                await rate_limiter.acquire()
                # Stagger request starts to avoid burst (NVIDIA per-second limit).
                # The RPM limiter controls throughput; this lock ensures requests
                # are spaced by min_request_interval seconds so they don't all
                # fire simultaneously and hit the burst cap.
                async with request_gate:
                    now = asyncio.get_event_loop().time()
                    gap = min_request_interval - (now - last_fire_time[0])
                    if gap > 0:
                        await asyncio.sleep(gap)
                    last_fire_time[0] = asyncio.get_event_loop().time()
                response = await _api_call_with_429_retry(
                    client, messages, call_params
                )

                text = (response.choices[0].message.content or "").strip()
                last_response_text = text

                if not text:
                    raise ValueError("Empty response from model")

                # ── Strip markdown code fences ────────────────────────
                # Some models (e.g. GLM) wrap JSON in ```json ... ```
                if text.startswith("```"):
                    lines = text.splitlines()
                    end = -1 if lines[-1].strip() == "```" else len(lines)
                    text = "\n".join(lines[1:end]).strip()

                # ── JSON parse ────────────────────────────────────────
                raw = json.loads(text)

                # ── Pydantic validation ───────────────────────────────
                extracted = ExtractionResult.model_validate(raw)
                out = extracted.model_dump()

                # ── Token accounting ──────────────────────────────────
                usage = response.usage
                prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
                completion_tok = getattr(usage, "completion_tokens", 0) or 0
                # Reasoning tokens (thinking models — should be 0 here)
                reasoning_tok = 0
                if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
                    reasoning_tok = getattr(
                        usage.completion_tokens_details, "reasoning_tokens", 0
                    ) or 0

                # ── Build thread result for validator ─────────────────
                thread_result = {
                    "thread_id": tid,
                    "triples": out.get("triples", []),
                    "thread_summary": out.get("thread_summary", ""),
                    "department_tags": out.get("department_tags", ["general"]),
                    "sensitivity": out.get("sensitivity", "internal"),
                    "email_count": email_count,
                }

                # ── Pipeline validator ────────────────────────────────
                validated, rejected = validate_thread(
                    thread_result, name_lookup, persons_by_id
                )

                # ── Auto-corrections count ────────────────────────────
                auto_corrected = sum(
                    validated.get("_auto_corrections", {}).values()
                )
                rejected_reasons: list[str] = []
                for r in rejected:
                    rejected_reasons.extend(
                        [reason.split(":")[0] for reason in r.get("reasons", [])]
                    )

                elapsed_s = time.monotonic() - t0

                record = {
                    "thread_id": tid,
                    "teacher": teacher_obj.teacher,
                    "model": teacher_obj.model_id,
                    "email_count": email_count,
                    "triples": validated.get("triples", []),
                    "thread_summary": validated.get("thread_summary", ""),
                    "department_tags": validated.get("department_tags", ["general"]),
                    "sensitivity": validated.get("sensitivity", "internal"),
                    "validation": {
                        "approved": len(validated.get("triples", [])),
                        "rejected": len(rejected),
                        "auto_corrected": auto_corrected,
                        "rejected_reasons": list(set(rejected_reasons)),
                    },
                    "timing": {
                        "started_at": started_at,
                        "elapsed_s": round(elapsed_s, 2),
                    },
                    "tokens": {
                        "prompt": prompt_tok,
                        "completion": completion_tok,
                        "reasoning": reasoning_tok,
                        "total": prompt_tok + completion_tok + reasoning_tok,
                    },
                }

                return {
                    "success": True,
                    "thread_id": tid,
                    "record": record,
                    "validated": validated,
                    "rejected": rejected,
                    "attempts": attempt + 1,
                    "tokens": record["tokens"],
                    "elapsed_s": elapsed_s,
                    "started_at": started_at,
                }

            except AuthenticationError as e:
                # One-time fallback to NVIDIA_API_KEY1 for NVIDIA endpoints
                if not used_fallback and fallback_client is not None:
                    print(
                        f"  [AUTH] Primary key failed for {tid[:12]} — "
                        f"trying fallback key ...",
                        flush=True,
                    )
                    client = fallback_client
                    used_fallback = True
                    # Don't count this as a retry attempt
                    continue
                elapsed_s = time.monotonic() - t0
                return {
                    "success": False,
                    "thread_id": tid,
                    "error_type": "AuthenticationError",
                    "error_msg": str(e)[:300],
                    "attempts": attempt + 1,
                    "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
                    "elapsed_s": elapsed_s,
                    "started_at": started_at,
                }

            except ValidationError as e:
                attempt += 1
                if attempt < MAX_RETRIES:
                    error_summary = str(e)[:500]
                    # Append self-correction message to conversation
                    messages = [
                        {"role": "system",    "content": SYSTEM_PROMPT},
                        {"role": "user",      "content": user_prompt},
                        {"role": "assistant", "content": last_response_text},
                        {"role": "user",      "content": (
                            f"Your response had validation errors:\n"
                            f"{error_summary}\n\n"
                            f"Please fix and return valid JSON only."
                        )},
                    ]
                    await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
                else:
                    elapsed_s = time.monotonic() - t0
                    return {
                        "success": False,
                        "thread_id": tid,
                        "error_type": "ValidationError",
                        "error_msg": str(e)[:500],
                        "attempts": attempt,
                        "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
                        "elapsed_s": elapsed_s,
                        "started_at": started_at,
                    }

            except json.JSONDecodeError as e:
                attempt += 1
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
                else:
                    elapsed_s = time.monotonic() - t0
                    return {
                        "success": False,
                        "thread_id": tid,
                        "error_type": "JSONDecodeError",
                        "error_msg": str(e)[:300],
                        "attempts": attempt,
                        "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
                        "elapsed_s": elapsed_s,
                        "started_at": started_at,
                    }

            except asyncio.CancelledError:
                # Propagate cancellation (used by SIGINT handler)
                raise

            except Exception as e:
                attempt += 1
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt - 1]
                    print(
                        f"  [ERR] {tid[:12]} attempt {attempt} failed "
                        f"({type(e).__name__}: {str(e)[:80]}), retrying in {wait}s",
                        flush=True,
                    )
                    await asyncio.sleep(wait)
                else:
                    elapsed_s = time.monotonic() - t0
                    return {
                        "success": False,
                        "thread_id": tid,
                        "error_type": type(e).__name__,
                        "error_msg": str(e)[:300],
                        "attempts": attempt,
                        "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
                        "elapsed_s": elapsed_s,
                        "started_at": started_at,
                    }

    # Should never reach here
    elapsed_s = time.monotonic() - t0
    return {
        "success": False,
        "thread_id": tid,
        "error_type": "MaxRetriesExceeded",
        "error_msg": "Exhausted all retries",
        "attempts": MAX_RETRIES,
        "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
        "elapsed_s": elapsed_s,
        "started_at": started_at,
    }


# ══════════════════════════════════════════════════════════════════════
# PROGRESS DISPLAY
# ══════════════════════════════════════════════════════════════════════

def print_thread_line(
    result: dict,
    idx: int,
    total: int,
    run_start: float,
    total_completed: int,
    log_file,
) -> None:
    """Print a single-line progress update for a completed thread."""
    tid = result["thread_id"]
    elapsed = result["elapsed_s"]
    tokens = result["tokens"]
    wall = time.monotonic() - run_start

    # Throughput rate (threads/minute)
    rate = (total_completed / wall * 60) if wall > 0 else 0.0
    remaining = total - total_completed
    eta = _fmt_eta(remaining, rate)

    if result["success"]:
        rec = result["record"]
        approved = rec["validation"]["approved"]
        ec = rec.get("email_count", 0)
        prompt_t = tokens.get("prompt", 0)
        completion_t = tokens.get("completion", 0)
        line = (
            f"[{idx:>5}/{total}] ✓ {tid}  {ec}e  {elapsed:.1f}s  "
            f"in={_fmt_tokens(prompt_t)} out={_fmt_tokens(completion_t)}  "
            f"triples={len(rec.get('triples', []))} valid={approved}  "
            f"rate:{rate:.1f}/min  ETA:{eta}"
        )
    else:
        err = result.get("error_type", "?")
        line = (
            f"[{idx:>5}/{total}] ✗ {tid}  {elapsed:.1f}s  "
            f"ERROR:{err}  rate:{rate:.1f}/min  ETA:{eta}"
        )

    print(line, flush=True)
    if log_file:
        log_file.write(f"{_now_iso()} {line}\n")
        log_file.flush()


def print_summary_line(
    total_done: int,
    total: int,
    run_start: float,
    total_tokens: int,
    total_approved: int,
    total_triples_seen: int,
    teacher: str,
    log_file,
) -> None:
    """Print cumulative summary every SUMMARY_EVERY threads."""
    wall = time.monotonic() - run_start
    rate = (total_done / wall * 60) if wall > 0 else 0.0
    remaining = total - total_done
    eta = _fmt_eta(remaining, rate)

    wall_h = int(wall // 3600)
    wall_m = int((wall % 3600) // 60)
    wall_s = int(wall % 60)
    wall_fmt = (f"{wall_h}h {wall_m:02d}m {wall_s:02d}s"
                if wall_h else f"{wall_m}m {wall_s:02d}s")

    pass_rate = (
        f"{total_approved/total_triples_seen*100:.1f}%"
        if total_triples_seen > 0 else "n/a"
    )

    line = (
        f"── [{teacher}] {total_done}/{total} done ──  "
        f"wall:{wall_fmt}  tokens:{_fmt_tokens(total_tokens)}  "
        f"pass_rate:{pass_rate}  rate:{rate:.1f}/min  ETA:{eta}"
    )
    print(line, flush=True)
    if log_file:
        log_file.write(f"{_now_iso()} {line}\n")
        log_file.flush()


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Knowledge distillation runner — one teacher LLM per terminal"
    )
    parser.add_argument(
        "--teacher", required=True, choices=list(TEACHERS),
        help="Which teacher model to run",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None, metavar="N",
        help="Hard stop after N completed threads (default: run to completion)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="(Default behavior) continue from checkpoint if it exists",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Rerun only threads listed in failed.jsonl",
    )
    parser.add_argument(
        "--concurrency", type=int, default=None, metavar="N",
        help="Max concurrent requests (default: teacher's default)",
    )
    parser.add_argument(
        "--rpm", type=int, default=None, metavar="N",
        help="Override teacher's default RPM cap",
    )
    args = parser.parse_args()

    teacher_name = args.teacher
    teacher_obj = TEACHERS[teacher_name]
    out_dir = _output_dir(teacher_name)

    concurrency = args.concurrency or teacher_obj.default_concurrency
    rpm_cap = args.rpm or teacher_obj.rpm_cap

    print(f"╔══════════════════════════════════════════════════════╗")
    print(f"  Knowledge Distillation — teacher: {teacher_name}")
    print(f"  Model:       {teacher_obj.model_id}")
    print(f"  Concurrency: {concurrency}   RPM cap: {rpm_cap}")
    print(f"  Output dir:  {out_dir}")
    print(f"╚══════════════════════════════════════════════════════╝")
    print()

    # ── Load assignments ───────────────────────────────────────────────
    if not ASSIGNMENTS_PATH.exists():
        print("ERROR: assignments.json not found.")
        print("Run first:  python -m knowledge_distillation.allocator")
        sys.exit(1)

    assignments_data = json.loads(
        ASSIGNMENTS_PATH.read_text(encoding="utf-8")
    )
    teacher_assignments = {
        tid
        for tid, t in assignments_data["allocations"].items()
        if t == teacher_name
    }
    total_assigned = len(teacher_assignments)
    print(f"Assigned threads: {total_assigned:,}")

    # ── Load checkpoint ────────────────────────────────────────────────
    ckpt = load_checkpoint(teacher_name)
    completed_ids: set[str] = set(ckpt.get("completed_ids", []))
    failed_ids: set[str] = set(ckpt.get("failed_ids", []))

    # ── Crash-safety scan ──────────────────────────────────────────────
    labeled_ids = scan_labeled_for_crash_recovery(teacher_name)
    recovered = labeled_ids - completed_ids
    if recovered:
        print(
            f"[RECOVERY] Found {len(recovered)} threads in labeled.jsonl "
            f"not in checkpoint — adding to completed set."
        )
        completed_ids |= recovered
        ckpt["completed_ids"] = list(completed_ids)

    print(
        f"Checkpoint: {len(completed_ids)} done, {len(failed_ids)} failed"
    )

    # ── Check daily cap (Nemotron) ─────────────────────────────────────
    if check_and_update_daily_cap(teacher_obj, ckpt):
        print(
            f"\n⚠️  Daily cap reached for {teacher_name}: "
            f"{teacher_obj.daily_cap} requests/day.\n"
            f"   Wait for midnight UTC reset, then restart.\n"
            f"   Today ({ckpt.get('daily_date', '?')}): "
            f"{ckpt.get('daily_count', 0)} requests."
        )
        sys.exit(0)

    # ── Compute todo list ──────────────────────────────────────────────
    if args.retry_failed:
        # Retry mode: move failed_ids back to todo
        if not failed_ids:
            print("No failed threads to retry.")
            sys.exit(0)
        todo_ids = list(failed_ids)
        # Remove from failed state; they'll be re-added if they fail again
        failed_ids.clear()
        ckpt["failed_ids"] = []
        print(f"Retry mode: {len(todo_ids)} failed threads to retry.")
    else:
        todo_ids = list(teacher_assignments - completed_ids - failed_ids)
        todo_ids.sort()  # deterministic order

    if not todo_ids:
        print(
            f"All {total_assigned} threads already completed "
            f"({len(failed_ids)} failed). Nothing to do."
        )
        sys.exit(0)

    # Apply batch size limit
    if args.batch_size and len(todo_ids) > args.batch_size:
        todo_ids = todo_ids[: args.batch_size]

    total_todo = len(todo_ids)
    print(
        f"Todo: {total_todo} threads  "
        f"(remaining in allocation: {len(teacher_assignments - completed_ids - failed_ids):,})"
    )
    print()

    # ── Load thread data ───────────────────────────────────────────────
    if not FILTERED_PATH.exists():
        print(f"ERROR: Filtered JSONL not found: {FILTERED_PATH}")
        sys.exit(1)

    print(f"Loading thread data from {FILTERED_PATH} ...")
    all_threads = group_by_thread(FILTERED_PATH)
    # Filter to only assigned threads
    threads = {tid: all_threads[tid] for tid in todo_ids if tid in all_threads}
    missing = set(todo_ids) - set(threads)
    if missing:
        print(
            f"[WARN] {len(missing)} assigned thread IDs not found in "
            f"filtered data — skipping."
        )
        todo_ids = [tid for tid in todo_ids if tid in threads]

    print(f"Loaded {len(threads):,} threads to process.")

    # ── Load person registry ───────────────────────────────────────────
    if not REGISTRY_PATH.exists():
        print(f"[WARN] Person registry not found: {REGISTRY_PATH}")
        print("       Validation will use empty registry (person checks disabled).")
        email_to_person: dict = {}
        persons_by_id: dict = {}
        name_lookup: dict = {}
    else:
        email_to_person, persons_by_id = load_person_registry(
            "ezobooks", Path("output")
        )
        name_lookup = build_name_lookup(
            json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        )
        print(
            f"Person registry: {len(persons_by_id):,} persons, "
            f"{len(email_to_person):,} email mappings."
        )

    org_context = build_org_context(persons_by_id)

    # ── Create API clients ─────────────────────────────────────────────
    try:
        primary_client = teacher_obj.get_client(fallback=False)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    fallback_client: AsyncOpenAI | None = None
    if teacher_obj.has_fallback():
        fallback_client = teacher_obj.get_client(fallback=True)
        print("Fallback API key available.")

    semaphore = asyncio.Semaphore(concurrency)
    rate_limiter = AsyncRateLimiter(max_calls=rpm_cap, period=60.0)
    # Burst guard: stagger consecutive request starts so concurrent coroutines
    # don't all fire at the same millisecond.  Floor of 1.0s prevents startup
    # burst even when rpm_cap is set very high (effectively unlimited).
    request_gate = asyncio.Lock()
    last_fire_time: list[float] = [0.0]
    min_request_interval = max(60.0 / rpm_cap, 1.0)

    # ── Open output files ──────────────────────────────────────────────
    labeled_path = out_dir / "labeled.jsonl"
    failed_path  = out_dir / "failed.jsonl"
    log_path     = out_dir / "progress.log"

    labeled_file = open(labeled_path, "a", encoding="utf-8")
    failed_file  = open(failed_path,  "a", encoding="utf-8")
    log_file     = open(log_path,     "a", encoding="utf-8")

    log_file.write(f"\n{'='*60}\n")
    log_file.write(
        f"{_now_iso()} Starting run: teacher={teacher_name} "
        f"todo={total_todo}\n"
    )
    log_file.flush()

    # ── Signal handling ────────────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _sigint_handler():
        if not shutdown_event.is_set():
            print(
                "\n\n[SIGINT] Ctrl-C received. Draining in-flight requests "
                "then saving checkpoint ...",
                flush=True,
            )
            shutdown_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, _sigint_handler)

    # ── Run ────────────────────────────────────────────────────────────
    run_start = time.monotonic()
    total_done_this_run = 0
    total_tokens_this_run = 0
    total_approved_this_run = 0
    total_triples_seen_this_run = 0
    session_start_s = ckpt.get("elapsed_s", 0.0)
    session_start_tokens = ckpt.get("tokens_total", 0)

    # Create all tasks (semaphore limits actual concurrency)
    pending_tasks: dict[asyncio.Task, str] = {}  # task → thread_id

    for tid in todo_ids:
        if shutdown_event.is_set():
            break
        task = asyncio.create_task(
            run_thread(
                tid=tid,
                thread=threads[tid],
                teacher_obj=teacher_obj,
                primary_client=primary_client,
                fallback_client=fallback_client,
                semaphore=semaphore,
                rate_limiter=rate_limiter,
                name_lookup=name_lookup,
                persons_by_id=persons_by_id,
                email_to_person=email_to_person,
                org_context=org_context,
                request_gate=request_gate,
                last_fire_time=last_fire_time,
                min_request_interval=min_request_interval,
            )
        )
        pending_tasks[task] = tid

    # Process results as tasks complete
    try:
        while pending_tasks:
            # On shutdown: cancel all remaining tasks and drain them
            if shutdown_event.is_set():
                for t in list(pending_tasks.keys()):
                    t.cancel()
                # Wait up to 15s for in-flight tasks to acknowledge cancellation
                await asyncio.wait(list(pending_tasks.keys()), timeout=15.0)
                break

            # Wait for the next task to finish (with periodic checks for shutdown)
            done, _ = await asyncio.wait(
                list(pending_tasks.keys()),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=2.0,
            )

            for task in done:
                tid = pending_tasks.pop(task, None)
                if tid is None:
                    continue

                try:
                    result = task.result()
                except asyncio.CancelledError:
                    continue
                except Exception as e:
                    # Unexpected task failure
                    result = {
                        "success": False,
                        "thread_id": tid,
                        "error_type": type(e).__name__,
                        "error_msg": traceback.format_exc()[:500],
                        "attempts": 1,
                        "tokens": {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0},
                        "elapsed_s": 0.0,
                        "started_at": _now_iso(),
                    }

                total_done_this_run += 1

                if result["success"]:
                    rec = result["record"]
                    completed_ids.add(tid)
                    total_tokens_this_run += result["tokens"].get("total", 0)
                    approved = rec["validation"]["approved"]
                    raw_triples = approved + rec["validation"]["rejected"]
                    total_approved_this_run += approved
                    total_triples_seen_this_run += max(raw_triples, 1)

                    # Write record immediately
                    labeled_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    labeled_file.flush()

                    # Update daily cap counter
                    check_and_update_daily_cap(teacher_obj, ckpt, increment=True)

                else:
                    failed_ids.add(tid)
                    # Write to failed.jsonl
                    fail_entry = {
                        "thread_id": tid,
                        "teacher": teacher_name,
                        "error_type": result.get("error_type", "Unknown"),
                        "error_message": result.get("error_msg", ""),
                        "attempts": result.get("attempts", 0),
                        "failed_at": _now_iso(),
                    }
                    failed_file.write(json.dumps(fail_entry, ensure_ascii=False) + "\n")
                    failed_file.flush()

                # Progress line
                print_thread_line(
                    result=result,
                    idx=len(completed_ids),
                    total=total_assigned,
                    run_start=run_start,
                    total_completed=len(completed_ids),
                    log_file=log_file,
                )

                # Summary every N threads
                if total_done_this_run % SUMMARY_EVERY == 0:
                    print_summary_line(
                        total_done=len(completed_ids),
                        total=total_assigned,
                        run_start=run_start,
                        total_tokens=total_tokens_this_run,
                        total_approved=total_approved_this_run,
                        total_triples_seen=total_triples_seen_this_run,
                        teacher=teacher_name,
                        log_file=log_file,
                    )

                # Checkpoint every N completions
                if total_done_this_run % CHECKPOINT_EVERY == 0:
                    wall = time.monotonic() - run_start
                    ckpt.update({
                        "teacher": teacher_name,
                        "last_updated": _now_iso(),
                        "threads_total": total_assigned,
                        "threads_done": len(completed_ids),
                        "threads_failed": len(failed_ids),
                        # session_start_tokens is fixed at run start (no double-count)
                        "tokens_total": session_start_tokens + total_tokens_this_run,
                        "elapsed_s": round(session_start_s + wall, 1),
                        "completed_ids": list(completed_ids),
                        "failed_ids": list(failed_ids),
                    })
                    save_checkpoint(teacher_name, ckpt)

                # Daily cap check after each completion (nemotron)
                if teacher_obj.daily_cap and check_and_update_daily_cap(teacher_obj, ckpt):
                    print(
                        f"\n⚠️  Daily cap reached: {teacher_obj.daily_cap} "
                        f"requests today. Stopping cleanly.\n"
                        f"   Restart after midnight UTC.",
                        flush=True,
                    )
                    shutdown_event.set()

    except Exception as e:
        print(f"\n[FATAL] Unexpected error in main loop: {e}", flush=True)
        traceback.print_exc()

    finally:
        # ── Final checkpoint ───────────────────────────────────────────
        wall = time.monotonic() - run_start
        ckpt.update({
            "teacher": teacher_name,
            "last_updated": _now_iso(),
            "threads_total": total_assigned,
            "threads_done": len(completed_ids),
            "threads_failed": len(failed_ids),
            "tokens_total": session_start_tokens + total_tokens_this_run,
            "elapsed_s": round(session_start_s + wall, 1),
            "completed_ids": list(completed_ids),
            "failed_ids": list(failed_ids),
        })
        save_checkpoint(teacher_name, ckpt)

        labeled_file.close()
        failed_file.close()

        # ── Final summary ──────────────────────────────────────────────
        wall_h = int(wall // 3600)
        wall_m = int((wall % 3600) // 60)
        wall_s_int = int(wall % 60)
        wall_fmt = (f"{wall_h}h {wall_m:02d}m {wall_s_int:02d}s"
                    if wall_h else f"{wall_m}m {wall_s_int:02d}s")

        rate = (total_done_this_run / wall * 60) if wall > 0 else 0.0
        pass_rate = (
            f"{total_approved_this_run / total_triples_seen_this_run * 100:.1f}%"
            if total_triples_seen_this_run > 0 else "n/a"
        )

        summary = (
            f"\n{'='*60}\n"
            f"  DONE [{teacher_name}]  "
            f"this_run={total_done_this_run}  "
            f"total={len(completed_ids)}/{total_assigned}  "
            f"failed={len(failed_ids)}\n"
            f"  wall={wall_fmt}  "
            f"tokens={_fmt_tokens(total_tokens_this_run)}  "
            f"pass_rate={pass_rate}  "
            f"rate={rate:.1f}/min\n"
            f"  labeled.jsonl → {labeled_path}\n"
            f"{'='*60}"
        )
        print(summary, flush=True)
        log_file.write(f"{_now_iso()} {summary}\n")
        log_file.close()

    # Exit with code 130 if SIGINT caused shutdown
    if shutdown_event.is_set():
        sys.exit(130)


def run() -> None:
    from dotenv import load_dotenv
    load_dotenv()
    asyncio.run(main())


if __name__ == "__main__":
    run()
