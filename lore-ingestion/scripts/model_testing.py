"""
model_testing.py
────────────────
Live model benchmarking harness for triple extraction.

Loads real filtered email threads, runs them against a chosen NVIDIA-hosted
model at up to 40 RPM with async concurrency, streams per-thread results to
the terminal in real time, validates each response, then prints a full
summary and analysis.

Usage (run from lore-ingestion/):

    # ── DEFAULT MODELS ──────────────────────────────────
    # Kimi K2-Instruct (balance of speed & quality, no thinking)
    ../.venv/bin/python model_testing.py --limit 20

    # ── WITH THINKING/REASONING ─────────────────────────
    # GLM-4.7 (with thinking enabled, reasoning tokens tracked)
    ../.venv/bin/python model_testing.py --model z-ai/glm4.7 --thinking --limit 10

    # Nemotron 3 Super 120B (with thinking enabled, includes reasoning_budget)
    ../.venv/bin/python model_testing.py --model nvidia/nemotron-3-super-120b-a12b --thinking --limit 10

    # Step 3.5 Flash (StepFun reasoning model, thinking enabled)
    ../.venv/bin/python model_testing.py --model stepfun-ai/step-3.5-flash --thinking --limit 10

    # ── FAST / EFFICIENT MODELS ─────────────────────────
    # Qwen3-Next 80B (small context, fast inference)
    ../.venv/bin/python model_testing.py --model qwen/qwen3-next-80b-a3b-instruct --limit 20

    # Qwen3-Coder 480B (specialized for code/technical extraction)
    ../.venv/bin/python model_testing.py --model qwen/qwen3-coder-480b-a35b-instruct --limit 20

    # Kimi K2-Instruct 0905 (improved version, model-specific defaults)
    ../.venv/bin/python model_testing.py --model moonshotai/kimi-k2-instruct-0905 --limit 20

    # ── OTHER MODELS ────────────────────────────────────
    # DeepSeek V3.2 (disable thinking if enabled)
    ../.venv/bin/python model_testing.py --model deepseek-ai/deepseek-v3.2 --no-thinking --limit 20

    # ── BATCH TEST (all models, 10 threads each) ────────
    echo "Testing all models..." && \
    ../.venv/bin/python model_testing.py --model moonshotai/kimi-k2-instruct --limit 10 && \
    ../.venv/bin/python model_testing.py --model moonshotai/kimi-k2-instruct-0905 --limit 10 && \
    ../.venv/bin/python model_testing.py --model z-ai/glm4.7 --thinking --limit 10 && \
    ../.venv/bin/python model_testing.py --model nvidia/nemotron-3-super-120b-a12b --thinking --limit 10 && \
    ../.venv/bin/python model_testing.py --model stepfun-ai/step-3.5-flash --thinking --limit 10 && \
    ../.venv/bin/python model_testing.py --model qwen/qwen3-next-80b-a3b-instruct --limit 10 && \
    ../.venv/bin/python model_testing.py --model qwen/qwen3-coder-480b-a35b-instruct --limit 10 && \
    ../.venv/bin/python model_testing.py --model deepseek-ai/deepseek-v3.2 --limit 10

    # ── CUSTOM OPTIONS ──────────────────────────────────
    # Custom concurrency and RPM limit
    ../.venv/bin/python model_testing.py --model z-ai/glm4.7 --thinking --limit 30 --concurrency 8 --rpm 35

    # Specific thread IDs
    ../.venv/bin/python model_testing.py --thread-ids tid1,tid2,tid3

    # Specific org
    ../.venv/bin/python model_testing.py --org otherorgs --limit 15
"""

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import ValidationError

from common.config import get_settings

# ── project imports (run as a module from lore-ingestion/: python -m scripts.model_testing) ──
from common.logger import get_logger
from common.schemas_extraction import ExtractionResult
from pipeline.triple_extractor import (
    AsyncRateLimiter,
    SYSTEM_PROMPT,
    group_by_thread,
    load_person_registry,
    build_thread_prompt,
    build_org_context,
)
from pipeline.validator import (
    build_name_lookup,
    validate_thread,
    check_structural_rules,
)

log = get_logger("model_testing")

# ── ANSI colours ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

ORG = "ezobooks"
DATA_DIR = Path("output")
FILTERED_PATH = DATA_DIR / "filtered" / f"{ORG}_gmail_filtered.jsonl"
REGISTRY_PATH = DATA_DIR / "knowledge" / f"{ORG}_person_registry.json"


# ══════════════════════════════════════════════════════════════════════
# MODEL PROFILES — per-model call params
# ══════════════════════════════════════════════════════════════════════

MODEL_PROFILES = {
    # Default/fallback
    "default": {
        "temperature": 0.3,
        "top_p": 0.95,
        "max_tokens": 8192,
        "thinking_capable": False,
    },
    # Kimi K-2 and K-2.5
    "moonshotai/kimi-k2-instruct": {
        "temperature": 0.6,
        "top_p": 0.9,
        "max_tokens": 4096,
        "thinking_capable": False,
    },
    "moonshotai/kimi-k2-instruct-0905": {
        "temperature": 0.6,
        "top_p": 0.9,
        "max_tokens": 4096,
        "thinking_capable": False,
    },
    # Nemotron with thinking
    "nvidia/nemotron-3-super-120b-a12b": {
        "temperature": 1.0,
        "top_p": 0.95,
        "max_tokens": 16384,
        "thinking_capable": True,
        "reasoning_budget": 16384,
    },
    # GLM-4.7 with thinking
    "z-ai/glm4.7": {
        "temperature": 1.0,
        "top_p": 1.0,
        "max_tokens": 16384,
        "thinking_capable": True,
        "clear_thinking": False,
    },
    # Step 3.5 Flash (StepFun reasoning model)
    "stepfun-ai/step-3.5-flash": {
        "temperature": 1.0,
        "top_p": 0.9,
        "max_tokens": 16384,
        "thinking_capable": True,
    },
    # Qwen3-Next 80B
    "qwen/qwen3-next-80b-a3b-instruct": {
        "temperature": 0.6,
        "top_p": 0.7,
        "max_tokens": 4096,
        "thinking_capable": False,
    },
    # Qwen3-Coder 480B (specialized for code extraction)
    "qwen/qwen3-coder-480b-a35b-instruct": {
        "temperature": 0.7,
        "top_p": 0.8,
        "max_tokens": 1024,
        "thinking_capable": False,
    },
    # DeepSeek V3.2
    "deepseek-ai/deepseek-v3.2": {
        "temperature": 0.3,
        "top_p": 0.95,
        "max_tokens": 8192,
        "thinking_capable": False,
    },
}


def get_model_profile(model: str) -> dict:
    """Get profile for a model, with fallback to default."""
    return MODEL_PROFILES.get(model, MODEL_PROFILES["default"])


def build_call_params(model: str, thinking_mode: str | None) -> dict:
    """Return kwargs for client.chat.completions.create() based on model.

    thinking_mode: "on" | "off" | None (None = model default)
    """
    profile = get_model_profile(model)
    params = {
        "temperature": profile["temperature"],
        "top_p": profile["top_p"],
        "max_tokens": profile["max_tokens"],
    }

    # Handle thinking modes
    if thinking_mode == "on":
        if not profile.get("thinking_capable"):
            print(f"{YELLOW}Warning: {model} doesn't support thinking, ignoring --thinking{RESET}")
        else:
            extra = {"chat_template_kwargs": {"enable_thinking": True}}
            if profile.get("clear_thinking") is not None:
                extra["chat_template_kwargs"]["clear_thinking"] = profile["clear_thinking"]
            if profile.get("reasoning_budget"):
                extra["reasoning_budget"] = profile["reasoning_budget"]
            params["extra_body"] = extra

    elif thinking_mode == "off":
        if profile.get("thinking_capable"):
            params["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False}
            }

    return params


# ══════════════════════════════════════════════════════════════════════
# PER-THREAD WORKER
# ══════════════════════════════════════════════════════════════════════

GREY = "\033[90m"   # dim grey for reasoning tokens

async def run_thread(
    idx: int,
    total: int,
    tid: str,
    thread: dict,
    client: AsyncOpenAI,
    model: str,
    thinking_mode: str | None,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    email_to_person: dict,
    persons_by_id: dict,
    org_context: str,
    name_lookup: dict,
    results: list,
    lock: asyncio.Lock,
):
    """Run extraction + validation for one thread, print live."""
    t0 = time.monotonic()
    email_count = len(thread.get("emails", []))
    subject = thread.get("subject", "?")[:60]

    async with semaphore:
        prompt = build_thread_prompt(thread, email_to_person, persons_by_id, org_context)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ]

        # ── API call (always streaming) ───────────────────────────────
        error = None
        raw_text = ""
        reasoning_text = ""
        tokens_in = tokens_out = tokens_reasoning = 0
        triples = []
        validated_count = rejected_count = 0
        summary = ""
        dept_tags = []
        sensitivity = ""
        parse_ok = False

        call_params = build_call_params(model, thinking_mode)

        try:
            await rate_limiter.acquire()
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **call_params,
            )

            # ── Collect streaming chunks ──────────────────────────────
            async for chunk in stream:
                # Usage summary arrives in the final chunk
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    tokens_in  = getattr(u, "prompt_tokens",     0) or 0
                    tokens_out = getattr(u, "completion_tokens", 0) or 0
                    # Some providers report thinking tokens separately
                    ct = getattr(u, "completion_tokens_details", None)
                    if ct:
                        tokens_reasoning = getattr(ct, "reasoning_tokens", 0) or 0

                if not getattr(chunk, "choices", None):
                    continue
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                # reasoning_content — thinking trace (GLM-4.7)
                rc = getattr(delta, "reasoning_content", None)
                if rc:
                    reasoning_text += rc

                # regular content — the JSON answer
                if getattr(delta, "content", None):
                    raw_text += delta.content

            raw_text = raw_text.strip()

            # ── Parse ─────────────────────────────────────────────────
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

            raw_json = json.loads(cleaned)
            validated_obj = ExtractionResult.model_validate(raw_json)
            output = validated_obj.model_dump()
            output["thread_id"] = tid
            output["email_count"] = email_count
            output["_tokens"] = tokens_in + tokens_out
            parse_ok = True

            triples = output.get("triples", [])
            summary = output.get("thread_summary", "")
            dept_tags = output.get("department_tags", [])
            sensitivity = output.get("sensitivity", "")

            # ── Validator gate ────────────────────────────────────────
            validated_thread, rejected_triples = validate_thread(
                output, name_lookup, persons_by_id
            )
            validated_count = len(validated_thread.get("triples", []))
            rejected_count  = len(rejected_triples)

        except asyncio.TimeoutError:
            error = "TIMEOUT"
        except json.JSONDecodeError as e:
            error = f"JSON_PARSE: {e}"
        except ValidationError as e:
            err_details = "; ".join(
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in e.errors()[:3]
            )
            error = f"SCHEMA ({e.error_count()} errors): {err_details}"
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:120]}"

        elapsed = time.monotonic() - t0

    # ── Live print (serialised via lock so lines don't interleave) ────
    async with lock:
        status_icon = f"{GREEN}✓{RESET}" if not error else f"{RED}✗{RESET}"
        triple_colour = GREEN if validated_count > 0 else (YELLOW if not error else RED)
        think_str = (f"  {GREY}think={tokens_reasoning}{RESET}"
                     if tokens_reasoning else "")

        print(
            f"{BOLD}[{idx:>3}/{total}]{RESET} {status_icon}  "
            f"{CYAN}{tid[:16]}{RESET}  "
            f"{DIM}{email_count}e{RESET}  "
            f"{elapsed:5.1f}s  "
            f"in={tokens_in} out={tokens_out}{think_str}  "
            f"triples={triple_colour}{len(triples)}{RESET} "
            f"valid={GREEN}{validated_count}{RESET} "
            f"rejected={RED}{rejected_count}{RESET}  "
            f"{DIM}{subject}{RESET}"
        )

        if error:
            print(f"         {RED}ERROR: {error}{RESET}")
        elif triples:
            for t in triples:
                subj = t.get("subject", {})
                obj  = t.get("object",  {})
                s_type = subj.get("type", "?")
                o_type = obj.get("type", "?")
                s_name = subj.get("name") or subj.get("description", "")
                o_name = obj.get("name") or obj.get("description", "")
                pred   = t.get("predicate", "?")
                print(
                    f"         {DIM}[{s_type}]{RESET} {s_name[:40]!r}  "
                    f"─{BOLD}{pred}{RESET}─▶  "
                    f"{DIM}[{o_type}]{RESET} {o_name[:40]!r}"
                )
            if summary and summary != "No extractable knowledge.":
                print(f"         {DIM}↳ {summary[:120]}{RESET}")

    # ── Collect for analysis ──────────────────────────────────────────
    results.append({
        "thread_id": tid,
        "email_count": email_count,
        "subject": subject,
        "elapsed": elapsed,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_reasoning": tokens_reasoning,
        "error": error,
        "parse_ok": parse_ok,
        "triples_total": len(triples),
        "validated": validated_count,
        "rejected": rejected_count,
        "dept_tags": dept_tags,
        "sensitivity": sensitivity,
        "summary": summary,
        "raw_triples": triples,
    })


# ══════════════════════════════════════════════════════════════════════
# ANALYSIS REPORT
# ══════════════════════════════════════════════════════════════════════

def print_analysis(results: list, model: str, wall_time: float,
                   dt_start: str = "", t_script_start: float = 0):
    """Print comprehensive analysis with timing info."""
    import datetime

    total = len(results)
    errors = [r for r in results if r["error"]]
    ok     = [r for r in results if not r["error"]]
    with_triples = [r for r in ok if r["triples_total"] > 0]
    empty        = [r for r in ok if r["triples_total"] == 0]

    total_triples      = sum(r["triples_total"]    for r in results)
    total_validated    = sum(r["validated"]         for r in results)
    total_rejected     = sum(r["rejected"]          for r in results)
    total_tokens_in    = sum(r["tokens_in"]         for r in results)
    total_tokens_out   = sum(r["tokens_out"]        for r in results)
    total_tokens_think = sum(r.get("tokens_reasoning", 0) for r in results)

    avg_elapsed = sum(r["elapsed"] for r in results) / total if total else 0
    elapsed_ok  = [r["elapsed"] for r in ok]
    max_elapsed = max(elapsed_ok) if elapsed_ok else 0
    min_elapsed = min(elapsed_ok) if elapsed_ok else 0

    # Predicate breakdown
    pred_counts: dict[str, int] = {}
    node_type_counts: dict[str, int] = {}
    dept_counts: dict[str, int] = {}
    sensitivity_counts: dict[str, int] = {}

    for r in results:
        for t in r.get("raw_triples", []):
            pred = t.get("predicate", "UNKNOWN")
            pred_counts[pred] = pred_counts.get(pred, 0) + 1
            for side in ("subject", "object"):
                node = t.get(side, {})
                nt = node.get("node_type", node.get("type", "UNKNOWN"))
                node_type_counts[nt] = node_type_counts.get(nt, 0) + 1
        for tag in r.get("dept_tags", []):
            dept_counts[tag] = dept_counts.get(tag, 0) + 1
        s = r.get("sensitivity", "")
        if s:
            sensitivity_counts[s] = sensitivity_counts.get(s, 0) + 1

    # Validation pass rate
    val_pct = (total_validated / total_triples * 100) if total_triples else 0
    err_pct = (len(errors) / total * 100) if total else 0

    div  = "═" * 64
    div2 = "─" * 64

    print(f"\n{BOLD}{div}{RESET}")
    print(f"{BOLD}  EXTRACTION ANALYSIS  —  {model}{RESET}")
    print(f"{BOLD}{div}{RESET}")

    print(f"\n{BOLD}THROUGHPUT & TIMING{RESET}")
    if dt_start:
        dt_end = datetime.datetime.now().isoformat()
        print(f"  Start time          : {dt_start}")
        print(f"  End time            : {dt_end}")
    print(f"  Threads tested      : {total}")
    print(f"  Wall time (API)     : {wall_time:.1f}s")
    if t_script_start:
        script_elapsed = time.monotonic() - t_script_start
        print(f"  Total script time   : {script_elapsed:.1f}s  (including I/O and logging)")
    print(f"  Avg latency         : {avg_elapsed:.1f}s / thread")
    print(f"  Min / Max latency   : {min_elapsed:.1f}s / {max_elapsed:.1f}s")
    print(f"  Effective RPM       : {total / (wall_time / 60):.1f}")

    print(f"\n{BOLD}RELIABILITY{RESET}")
    print(f"  Successes           : {GREEN}{len(ok)}/{total}{RESET}  ({100-err_pct:.0f}%)")
    print(f"  Errors              : {RED}{len(errors)}/{total}{RESET}  ({err_pct:.0f}%)")
    if errors:
        by_type: dict[str, int] = {}
        for r in errors:
            key = r["error"].split(":")[0] if r["error"] else "UNKNOWN"
            by_type[key] = by_type.get(key, 0) + 1
        for etype, cnt in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"    {RED}{etype}{RESET}: {cnt}")

    print(f"\n{BOLD}EXTRACTION QUALITY{RESET}")
    print(f"  Threads with triples: {with_triples and len(with_triples) or 0}/{len(ok)}"
          f"  ({len(with_triples)/len(ok)*100:.0f}%)" if ok else "  n/a")
    print(f"  Empty threads       : {len(empty)}")
    print(f"  Total triples       : {total_triples}")
    print(f"  Avg triples/thread  : {total_triples/total:.1f}")
    print(f"  Avg triples/thread (non-empty): "
          f"{total_triples/len(with_triples):.1f}" if with_triples else "  n/a")
    print(f"  Validator pass rate : {GREEN}{total_validated}{RESET}/{total_triples} "
          f"({val_pct:.0f}%)")
    print(f"  Validator rejected  : {RED}{total_rejected}{RESET}")

    print(f"\n{BOLD}TOKEN USAGE{RESET}")
    print(f"  Total prompt tokens : {total_tokens_in:,}")
    print(f"  Total output tokens : {total_tokens_out:,}")
    if total_tokens_think:
        print(f"  Thinking tokens     : {GREY}{total_tokens_think:,}{RESET}  (included in output)")
    print(f"  Total tokens        : {total_tokens_in+total_tokens_out:,}")
    if ok:
        avg_in  = total_tokens_in  / len(ok)
        avg_out = total_tokens_out / len(ok)
        think_avg = (f"  Avg think tokens    : {total_tokens_think/len(ok):.0f} per thread\n"
                     if total_tokens_think else "")
        print(f"  Avg prompt / output : {avg_in:.0f} / {avg_out:.0f} per thread")
        if think_avg:
            print(think_avg, end="")

    if pred_counts:
        print(f"\n{BOLD}PREDICATE DISTRIBUTION{RESET}")
        for pred, cnt in sorted(pred_counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(cnt, 40)
            print(f"  {pred:<18} {cnt:>4}  {bar}")

    if node_type_counts:
        print(f"\n{BOLD}NODE TYPE DISTRIBUTION{RESET}")
        for nt, cnt in sorted(node_type_counts.items(), key=lambda x: -x[1]):
            bar = "█" * min(cnt, 40)
            print(f"  {nt:<18} {cnt:>4}  {bar}")

    if dept_counts:
        print(f"\n{BOLD}DEPARTMENT BREAKDOWN{RESET}")
        for dept, cnt in sorted(dept_counts.items(), key=lambda x: -x[1]):
            print(f"  {dept:<18} {cnt:>4}")

    if sensitivity_counts:
        print(f"\n{BOLD}SENSITIVITY BREAKDOWN{RESET}")
        for sens, cnt in sorted(sensitivity_counts.items(), key=lambda x: -x[1]):
            print(f"  {sens:<18} {cnt:>4}")

    # Slowest threads
    slowest = sorted(ok, key=lambda x: -x["elapsed"])[:5]
    if slowest:
        print(f"\n{BOLD}TOP 5 SLOWEST THREADS{RESET}")
        for r in slowest:
            print(f"  {r['elapsed']:5.1f}s  {r['thread_id'][:16]}  {r['subject'][:50]}")

    # Best extraction examples
    best = sorted(with_triples, key=lambda x: -x["validated"])[:3]
    if best:
        print(f"\n{BOLD}TOP 3 RICHEST EXTRACTIONS{RESET}")
        for r in best:
            print(f"  {r['validated']} valid triples  "
                  f"{r['thread_id'][:16]}  {r['subject'][:50]}")
            if r.get("summary") and r["summary"] != "No extractable knowledge.":
                print(f"    {DIM}{r['summary'][:120]}{RESET}")

    print(f"\n{BOLD}{div}{RESET}\n")


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

async def main():
    import datetime
    t_script_start = time.monotonic()
    dt_start = datetime.datetime.now().isoformat()

    parser = argparse.ArgumentParser(
        description="Live model benchmarking harness for triple extraction"
    )
    parser.add_argument(
        "--model", default="moonshotai/kimi-k2-instruct",
        help="NVIDIA model ID (default: moonshotai/kimi-k2-instruct)",
    )
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Number of threads to test (default: 20)",
    )
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="Max simultaneous in-flight requests (default: 5)",
    )
    parser.add_argument(
        "--rpm", type=int, default=38,
        help="Max requests per minute — stay 2 below hard cap (default: 38)",
    )
    parser.add_argument(
        "--thread-ids", default="",
        help="Comma-separated list of specific thread IDs to test",
    )
    parser.add_argument(
        "--org", default=ORG,
        help=f"Org name (default: {ORG})",
    )
    think_group = parser.add_mutually_exclusive_group()
    think_group.add_argument(
        "--thinking", action="store_true", default=False,
        help="Enable thinking/reasoning mode (GLM-4.7). Sets temp=1, max_tokens=16384.",
    )
    think_group.add_argument(
        "--no-thinking", action="store_true", default=False,
        help="Explicitly disable thinking (Nemotron, DeepSeek). Passes enable_thinking=False.",
    )
    args = parser.parse_args()

    thinking_mode: str | None = None
    if args.thinking:
        thinking_mode = "on"
    elif args.no_thinking:
        thinking_mode = "off"

    # ── Resolve paths ─────────────────────────────────────────────────
    org = args.org
    filtered_path = DATA_DIR / "filtered" / f"{org}_gmail_filtered.jsonl"
    registry_path = DATA_DIR / "knowledge" / f"{org}_person_registry.json"

    if not filtered_path.exists():
        print(f"{RED}Filtered JSONL not found: {filtered_path}{RESET}")
        sys.exit(1)

    # ── Load data ─────────────────────────────────────────────────────
    print(f"{BOLD}Loading threads…{RESET}", end=" ", flush=True)
    threads = group_by_thread(filtered_path)
    print(f"{len(threads):,} threads")

    registry = {}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text())
    email_to_person, persons_by_id = (
        registry.get("email_to_person_id", {}),
        {p["person_id"]: p for p in registry.get("persons", [])},
    ) if registry else ({}, {})

    name_lookup  = build_name_lookup(registry) if registry else {}
    org_context  = build_org_context(persons_by_id)

    # ── Select threads ────────────────────────────────────────────────
    if args.thread_ids:
        tids = [t.strip() for t in args.thread_ids.split(",") if t.strip()]
        selected = {tid: threads[tid] for tid in tids if tid in threads}
        missing  = [tid for tid in tids if tid not in threads]
        if missing:
            print(f"{YELLOW}Warning: {len(missing)} thread IDs not found{RESET}")
    else:
        tids = list(threads.keys())[: args.limit]
        selected = {tid: threads[tid] for tid in tids}

    total = len(selected)
    model = args.model

    think_label = {"on": "  THINKING : ON", "off": "  THINKING : OFF"}.get(thinking_mode, "")
    print(f"\n{BOLD}{CYAN}{'─'*64}{RESET}")
    print(f"{BOLD}  MODEL : {model}{RESET}")
    print(f"{BOLD}  THREADS : {total}   CONCURRENCY : {args.concurrency}   RPM cap : {args.rpm}{think_label}{RESET}")
    print(f"{BOLD}  START : {dt_start}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*64}{RESET}\n")

    # ── NVIDIA client ─────────────────────────────────────────────────
    s = get_settings()
    api_key = s.nvidia_api_key
    if not api_key:
        print(f"{RED}NVIDIA_API_KEY not set in .env{RESET}")
        sys.exit(1)

    client = AsyncOpenAI(
        base_url=s.nvidia_base_url,
        api_key=api_key,
        max_retries=0,
        timeout=300.0,
    )

    semaphore    = asyncio.Semaphore(args.concurrency)
    rate_limiter = AsyncRateLimiter(max_calls=args.rpm, period=60.0)
    results: list = []
    lock = asyncio.Lock()

    # ── Dispatch all threads ──────────────────────────────────────────
    t_start = time.monotonic()
    tasks = [
        run_thread(
            idx=i + 1,
            total=total,
            tid=tid,
            thread=thread,
            client=client,
            model=model,
            thinking_mode=thinking_mode,
            semaphore=semaphore,
            rate_limiter=rate_limiter,
            email_to_person=email_to_person,
            persons_by_id=persons_by_id,
            org_context=org_context,
            name_lookup=name_lookup,
            results=results,
            lock=lock,
        )
        for i, (tid, thread) in enumerate(selected.items())
    ]
    await asyncio.gather(*tasks)
    wall_time = time.monotonic() - t_start

    # ── Full analysis ─────────────────────────────────────────────────
    print_analysis(results, model, wall_time, dt_start, t_script_start)


if __name__ == "__main__":
    asyncio.run(main())
