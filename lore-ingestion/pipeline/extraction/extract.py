"""
pipeline/extraction/extract.py
──────────────────────────────
Core async extraction logic: per-thread extract, self-correction,
batch processing, and the full `extract_all` driver.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI
from pydantic import ValidationError

from common.config import get_settings, validate_for
from common.logger import get_logger
from common.schemas_extraction import ExtractionResult
from common.scopes import person_email
from common.sources import caps_for
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from infra.llm import get_chat_provider
from pipeline.validator import (
    validate_thread, build_name_lookup, check_structural_rules,
)

from pipeline.extraction.checkpoint import (
    _load_checkpoint, _save_checkpoint,
)
from pipeline.extraction.grouping import (
    group_by_thread, load_person_registry,
)
from pipeline.extraction.output import _save_output
from pipeline.extraction.prompts import (
    CORRECTION_PROMPT, SYSTEM_PROMPT,
    build_org_context, build_thread_prompt,
)
from pipeline.extraction.rate_limit import (
    AsyncRateLimiter, TokenAwareRateLimiter, estimate_request_tokens,
)

log = get_logger("pipeline.triple_extractor")


# ── Single-thread extraction ────────────────────────────────────────

MAX_RETRIES = 3
RETRY_BACKOFF = [2, 4, 8]          # seconds — generic transient errors
RATE_LIMIT_BACKOFF = [15, 30, 60]  # seconds — 429 / throttling (longer)

# Per-call completion-token estimate for the tokens/min budget. The
# reservation is reconciled to the response's real usage afterward, so
# this only needs to be a reasonable starting guess (most threads emit
# far fewer than the 8192 max_tokens ceiling).
COMPLETION_TOKEN_ESTIMATE = 1500

# Max output tokens requested per extraction call (DeepSeek V3.2 caps at 8K).
MAX_COMPLETION_TOKENS = 8192


def _rate_limit_retry_after(err: Exception) -> Optional[float]:
    """If `err` is a rate-limit/throttling error, return the suggested
    wait (Retry-After seconds) or 0.0 when it's rate-limited but carries
    no hint. Return None when `err` is not a rate-limit error.

    Covers Bedrock `ThrottlingException` (surfaced by the OpenAI SDK as
    `openai.RateLimitError`, HTTP 429) and generic 429 strings.
    """
    status = getattr(err, "status_code", None) or getattr(
        getattr(err, "response", None), "status_code", None)
    text = str(err).lower()
    is_rl = (
        type(err).__name__ == "RateLimitError"
        or status == 429
        or "429" in text
        or "throttl" in text
        or "rate limit" in text
        or "too many requests" in text
    )
    if not is_rl:
        return None
    # Try to honor Retry-After from the HTTP response headers.
    resp = getattr(err, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        try:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra is not None:
                return float(ra)
        except (TypeError, ValueError):
            pass
    return 0.0


def _reconcile_usage(rate_limiter, reservation, response) -> int:
    """Rewrite the limiter reservation with the call's real token usage."""
    usage = getattr(response, "usage", None)
    total = 0
    if usage:
        total = (usage.prompt_tokens or 0) + (usage.completion_tokens or 0)
    rate_limiter.reconcile(reservation, total)
    return total


def _populate_outlook_scopes(threads: dict) -> None:
    """Stamp person:{email} scope_ids onto Outlook Mail threads.

    Outlook Mail has no per-message channel ACL; scope is derived from
    participant actor emails across the thread.
    """
    for thread in threads.values():
        if thread.get("scope_id") or thread.get("scope_ids"):
            continue
        emails = thread.get("emails", [])
        if not emails or emails[0].get("source") != "outlook_mail":
            continue
        unioned: set[str] = set()
        for event in emails:
            for actor in event.get("actors", []) or []:
                email_addr = (actor.get("email") or "").lower().strip()
                if email_addr:
                    unioned.add(person_email(email_addr))
        if unioned:
            participant_scopes = sorted(unioned)
            thread["scope_ids"] = participant_scopes
            thread["scope_id"] = participant_scopes[0]


def _copy_thread_metadata(result: dict, thread: dict | None) -> dict:
    if not thread:
        return result
    for _k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref", "source_url", "url", "project", "raw"):
        if thread.get(_k) is not None:
            result[_k] = thread.get(_k)
    return result



def _parse_json_response(text: str) -> dict:
    """Parse an LLM response that may include code fences or prose.

    The extractor prefers raw JSON, but some providers wrap the payload in
    markdown fences or add leading/trailing commentary. This helper strips
    common wrappers before falling back to a best-effort JSON substring.
    """
    candidate = text.strip()

    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(candidate[start:end + 1])
        raise


def _make_error_result(tid: str, error_type: str, msg: str, thread: dict | None = None) -> dict:
    result = {
        "thread_id": tid,
        "triples": [],
        "thread_summary": f"{error_type}: {msg[:100]}",
        "department_tags": ["general"],
        "sensitivity": "internal",
        "_error": f"{error_type}: {msg[:500]}",
    }
    return _copy_thread_metadata(result, thread)


async def _extract_one_jira(
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    tid: str,
    thread: dict,
    org: str = "",
) -> tuple[str, dict]:
    """Jira extraction: deterministic skeleton + optional LLM prose pass.

    Phase 1 — deterministic builder: always runs, no LLM cost, builds
    REPORTED_BY/ASSIGNED_TO/epic/sprint/component/label edges from fields.

    Phase 2 — LLM prose pass: runs only when the issue has ≥ PROSE_MIN_CHARS
    of description+comment content. Uses the Jira-specific system prompt that
    forbids re-extracting structured facts. Falls back gracefully to the
    deterministic result if the LLM call fails — never returns an error for
    Jira issues (the skeleton is always usable).
    """
    from pipeline.jira.deterministic import build_deterministic_triples
    from pipeline.jira.prose_prompts import (
        JIRA_SYSTEM_PROMPT, PROSE_MIN_CHARS,
        build_jira_thread_prompt, build_structured_context, extract_prose_body,
    )

    # ── Phase 1: deterministic ────────────────────────────────────────────────
    det_result = build_deterministic_triples(thread, org=org)
    prose_body = extract_prose_body(thread)

    # ── Cost gate ─────────────────────────────────────────────────────────────
    if len(prose_body.strip()) < PROSE_MIN_CHARS:
        det_result["_source"] = "deterministic_only"
        log.debug("Jira %s: deterministic only (prose < %d chars)", tid, PROSE_MIN_CHARS)
        return tid, det_result

    # ── Phase 2: LLM prose pass ───────────────────────────────────────────────
    structured_ctx = build_structured_context(thread, thread.get("emails", []))
    user_prompt = build_jira_thread_prompt(prose_body, structured_ctx)
    messages = [
        {"role": "system", "content": JIRA_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    est_tokens = estimate_request_tokens(messages, COMPLETION_TOKEN_ESTIMATE)
    issue_node = det_result.get("_issue_node") or {"type": "Issue", "name": tid}

    async with semaphore:
        for attempt in range(MAX_RETRIES):
            try:
                _resv = await rate_limiter.acquire(est_tokens)
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.3,
                    top_p=0.95,
                    max_tokens=MAX_COMPLETION_TOKENS,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                text = (response.choices[0].message.content or "").strip()
                _reconcile_usage(rate_limiter, _resv, response)
                if not text:
                    raise ValueError("Empty response from LLM")

                raw_json = _parse_json_response(text)
                validated = ExtractionResult.model_validate(raw_json)
                prose_data = validated.model_dump()

                # Tag LLM triples + add DERIVED_FROM provenance anchors
                prose_triples: list[dict] = []
                for t in prose_data.get("triples", []):
                    t["derived"] = "llm"
                    prose_triples.append(t)
                    # Anchor each LLM-derived node back to its source Issue
                    prose_triples.append({
                        "subject": t["subject"],
                        "predicate": "DERIVED_FROM",
                        "object": issue_node,
                        "evidence_sentence": f"Extracted from prose of {tid}",
                        "derived": "provenance",
                    })

                tokens = (response.usage.prompt_tokens + response.usage.completion_tokens
                          if response.usage else 0)

                result = {
                    "thread_id": tid,
                    "triples": det_result["triples"] + prose_triples,
                    "thread_summary": (prose_data.get("thread_summary")
                                       or det_result.get("thread_summary", "")),
                    "department_tags": (prose_data.get("department_tags")
                                        or det_result.get("department_tags", ["general"])),
                    "sensitivity": (prose_data.get("sensitivity")
                                    or det_result.get("sensitivity", "internal")),
                    "email_count": len(thread.get("emails", [])),
                    "_source": "deterministic+llm",
                    "_tokens": tokens,
                }
                for k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref",
                          "source_url", "url", "project"):
                    if thread.get(k) is not None:
                        result[k] = thread[k]

                log.debug("Jira %s: %d det + %d prose triples, %d tokens",
                          tid, len(det_result["triples"]),
                          len(prose_data.get("triples", [])), tokens)
                return tid, result

            except (ValidationError, json.JSONDecodeError) as e:
                if attempt < MAX_RETRIES - 1:
                    log.warning("Jira %s attempt %d: parse/validation error (%s), retrying",
                                tid[:12], attempt + 1, str(e)[:60])
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
                else:
                    log.warning("Jira %s: LLM prose failed after %d attempts (%s), "
                                "returning deterministic skeleton",
                                tid[:12], MAX_RETRIES, str(e)[:80])
                    det_result["_source"] = "deterministic_llm_failed"
                    return tid, det_result

            except Exception as e:
                retry_after = _rate_limit_retry_after(e)
                if attempt < MAX_RETRIES - 1:
                    wait = retry_after or RETRY_BACKOFF[attempt]
                    log.warning("Jira %s attempt %d: %s, retrying in %.0fs",
                                tid[:12], attempt + 1, str(e)[:60], wait)
                    await asyncio.sleep(wait)
                else:
                    log.warning("Jira %s: LLM prose failed after %d attempts (%s), "
                                "returning deterministic skeleton",
                                tid[:12], MAX_RETRIES, str(e)[:80])
                    det_result["_source"] = "deterministic_llm_failed"
                    return tid, det_result

    det_result["_source"] = "deterministic_llm_failed"
    return tid, det_result


async def extract_one(
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    tid: str,
    thread: dict,
    email_to_person: dict,
    persons_by_id: dict,
    org_context: str = "",
) -> tuple[str, dict]:
    """Extract triples from a single thread with retries.

    Routes structured sources (Jira) through the deterministic + prose pass
    via _extract_one_jira. Unstructured sources (Gmail, Slack) use the
    existing LLM-first path.

    Uses response_format=json_object for structured output,
    then validates against Pydantic schema. On validation failure,
    retries with the error message appended so the model can
    self-correct.
    """
    source = (thread.get("emails") or [{}])[0].get("source", "")
    if caps_for(source).structured:
        org = (thread.get("emails") or [{}])[0].get("org", "")
        return await _extract_one_jira(
            client, model, semaphore, rate_limiter, tid, thread, org=org)

    async with semaphore:
        user_prompt = build_thread_prompt(
            thread, email_to_person, persons_by_id, org_context)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        est_tokens = estimate_request_tokens(messages, COMPLETION_TOKEN_ESTIMATE)
        for attempt in range(MAX_RETRIES):
            try:
                # Reserve RPM (+TPM for Bedrock); reconciled to real usage below.
                _resv = await rate_limiter.acquire(est_tokens)
                # NOTE: response_format=json_object disabled for
                # DeepSeek via NVIDIA — triggers "Invalid grammar
                # request" on some payloads. JSON enforcement relies
                # on the system prompt plus Pydantic validation.
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.3,
                    top_p=0.95,
                    max_tokens=MAX_COMPLETION_TOKENS,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )

                text = (response.choices[0].message.content or "").strip()
                _reconcile_usage(rate_limiter, _resv, response)
                if not text:
                    raise ValueError("Empty response from model")

                # Parse JSON
                raw = _parse_json_response(text)

                # Validate with Pydantic
                validated = ExtractionResult.model_validate(raw)
                output = validated.model_dump()
                # Preserve thread-level scoping and citation metadata from
                # the grouped thread so downstream validation and builders
                # can emit ACL edges and source citations.
                for _k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref", "source_url", "url", "project", "raw"):
                    if thread.get(_k) is not None:
                        output[_k] = thread.get(_k)
                output["thread_id"] = tid
                output["email_count"] = len(thread.get("emails", []))

                tokens_prompt = (response.usage.prompt_tokens
                                 if response.usage else 0)
                tokens_completion = (response.usage.completion_tokens
                                     if response.usage else 0)
                output["_tokens"] = tokens_prompt + tokens_completion

                log.debug("Thread %s: %d triples, %d tokens",
                          tid[:12],
                          len(output.get("triples", [])),
                          output["_tokens"])

                return tid, output

            except ValidationError as e:
                # Pydantic rejected the output — retry with error context
                if attempt < MAX_RETRIES - 1:
                    error_msg = str(e)
                    log.warning(
                        "Thread %s attempt %d: Pydantic validation "
                        "failed (%s), retrying",
                        tid[:12], attempt + 1, error_msg[:80])
                    # Append correction hint for the model
                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                        {"role": "assistant", "content": text},
                        {"role": "user",
                         "content": (
                             "Your response had validation errors:\n"
                             f"{error_msg[:500]}\n\n"
                             "Please fix and return valid JSON.")},
                    ]
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
                else:
                    log.error("Thread %s: Pydantic failed after %d "
                              "attempts: %s",
                              tid[:12], MAX_RETRIES, str(e)[:120])
                    return tid, _make_error_result(
                        tid, "pydantic_validation", str(e), thread)

            except json.JSONDecodeError as e:
                if attempt < MAX_RETRIES - 1:
                    log.warning(
                        "Thread %s attempt %d: JSON parse failed (%s)",
                        tid[:12], attempt + 1, str(e)[:80])
                    await asyncio.sleep(RETRY_BACKOFF[attempt])
                else:
                    log.error("Thread %s: JSON parse failed after %d "
                              "attempts: %s",
                              tid[:12], MAX_RETRIES, str(e)[:120])
                    return tid, _make_error_result(
                        tid, "json_parse", str(e), thread)

            except Exception as e:
                retry_after = _rate_limit_retry_after(e)
                is_rate_limit = retry_after is not None
                if attempt < MAX_RETRIES - 1:
                    if is_rate_limit:
                        wait = retry_after or RATE_LIMIT_BACKOFF[attempt]
                        log.warning("Thread %s attempt %d throttled (429), "
                                    "backing off %.1fs",
                                    tid[:12], attempt + 1, wait)
                    else:
                        wait = RETRY_BACKOFF[attempt]
                        log.warning("Thread %s attempt %d failed (%s), "
                                    "retrying in %ds",
                                    tid[:12], attempt + 1, str(e)[:80], wait)
                    await asyncio.sleep(wait)
                else:
                    log.error("Thread %s failed after %d attempts: %s",
                              tid[:12], MAX_RETRIES, str(e)[:120])
                    return tid, _make_error_result(
                        tid, "extraction_failed", str(e), thread)


# ── Self-correction ─────────────────────────────────────────────────

async def correct_one(
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    tid: str,
    thread: dict,
    original_result: dict,
    validation_errors: list[dict],
    email_to_person: dict,
    persons_by_id: dict,
    org_context: str = "",
) -> tuple[str, dict]:
    """Re-extract a thread with correction context from validation errors.

    Sends the model its own previous output alongside the specific
    validation errors, so it can fix targeted issues without
    re-doing the entire extraction from scratch.
    """
    async with semaphore:
        thread_prompt = build_thread_prompt(
            thread, email_to_person, persons_by_id, org_context)

        # Format original output (extraction fields only)
        original_json = json.dumps({
            "triples": original_result.get("triples", []),
            "thread_summary": original_result.get("thread_summary", ""),
            "department_tags": original_result.get("department_tags", []),
            "sensitivity": original_result.get("sensitivity", "internal"),
        }, ensure_ascii=False, indent=2)

        # Format validation errors for the model
        error_lines = []
        for err in validation_errors:
            triple = err.get("triple", {})
            reasons = err.get("reasons", [])
            s = triple.get("subject", {})
            o = triple.get("object", {})
            s_label = s.get("name") or s.get("description", "")
            o_label = o.get("name") or o.get("description", "")
            error_lines.append(
                f"  {s.get('type', '?')}:\"{s_label[:60]}\" "
                f"--[{triple.get('predicate', '')}]--> "
                f"{o.get('type', '?')}:\"{o_label[:60]}\"\n"
                f"    Errors: {', '.join(reasons)}")

        user_msg = (
            f"ORIGINAL EMAIL THREAD:\n{thread_prompt}\n\n"
            f"YOUR PREVIOUS EXTRACTION:\n{original_json}\n\n"
            f"VALIDATION ERRORS ({len(validation_errors)} triples "
            f"failed):\n" + "\n".join(error_lines) + "\n\n"
            f"Fix these issues and return the complete corrected "
            f"extraction as valid JSON."
        )

        messages = [
            {"role": "system", "content": CORRECTION_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        est_tokens = estimate_request_tokens(messages, COMPLETION_TOKEN_ESTIMATE)
        try:
            _resv = await rate_limiter.acquire(est_tokens)
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.2,
                top_p=0.95,
                max_tokens=MAX_COMPLETION_TOKENS,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )

            text = (response.choices[0].message.content or "").strip()
            _reconcile_usage(rate_limiter, _resv, response)
            if not text:
                log.warning("Thread %s: correction returned empty, "
                            "keeping original", tid[:12])
                return tid, original_result

            raw = _parse_json_response(text)
            validated = ExtractionResult.model_validate(raw)
            output = validated.model_dump()
            output["thread_id"] = tid
            output["email_count"] = original_result.get("email_count", 0)
            output["_corrected"] = True
            # Preserve thread-level metadata on corrected outputs as well
            for _k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref", "source_url", "url", "project", "raw"):
                if thread.get(_k) is not None:
                    output[_k] = thread.get(_k)

            tokens_prompt = (response.usage.prompt_tokens
                             if response.usage else 0)
            tokens_completion = (response.usage.completion_tokens
                                 if response.usage else 0)
            correction_tokens = tokens_prompt + tokens_completion
            output["_tokens"] = (original_result.get("_tokens", 0)
                                 + correction_tokens)
            output["_correction_tokens"] = correction_tokens

            log.debug("Thread %s: corrected → %d triples "
                      "(was %d, %d errors fixed)",
                      tid[:12], len(output.get("triples", [])),
                      len(original_result.get("triples", [])),
                      len(validation_errors))

            return tid, output

        except Exception as e:
            log.warning("Thread %s: correction failed (%s), "
                        "keeping original", tid[:12], str(e)[:80])
            return tid, original_result


# ── Batch processing ────────────────────────────────────────────────

async def _process_batch(
    batch: list[tuple[str, dict]],
    client: AsyncOpenAI,
    model: str,
    semaphore: asyncio.Semaphore,
    rate_limiter: AsyncRateLimiter,
    email_to_person: dict,
    persons_by_id: dict,
    org_context: str,
    name_lookup: dict,
) -> tuple[list[tuple[str, dict]], int, int]:
    """Extract a batch, validate results, and correct failures.

    Returns (results, corrections_made, correction_tokens).
    """
    # ── Phase 1: Extract all threads in parallel ─────────────────
    batch_results = await asyncio.gather(*[
        extract_one(client, model, semaphore, rate_limiter, t_id, t,
                    email_to_person, persons_by_id, org_context)
        for t_id, t in batch
    ])

    # ── Phase 2: Validate and collect failures ───────────────────
    batch_threads = dict(batch)
    correction_queue: list[tuple[str, dict, list[dict]]] = []

    for tid, result in batch_results:
        if "_error" in result or not result.get("triples"):
            continue

        # Structured sources (Jira) go through deterministic+prose in
        # _extract_one_jira; field triples bypass validation and the correction
        # prompt would use the Gmail-style builder and clobber them.
        source = (batch_threads.get(tid, {}).get("emails") or [{}])[0].get("source", "")
        if caps_for(source).structured:
            continue

        validated, rejected = validate_thread(
            result, name_lookup, persons_by_id)
        orphans = check_structural_rules(validated)
        all_errors = rejected + orphans

        if all_errors:
            correction_queue.append((tid, result, all_errors))

    # ── Phase 3: Correct failures in parallel ────────────────────
    corrections_made = 0
    correction_tokens = 0

    if correction_queue:
        log.info("  Correction pass: %d/%d threads need fixing",
                 len(correction_queue), len(batch))

        corrected_results = await asyncio.gather(*[
            correct_one(client, model, semaphore, rate_limiter, tid,
                        batch_threads[tid], result, errors,
                        email_to_person, persons_by_id, org_context)
            for tid, result, errors in correction_queue
        ])

        # Replace originals with corrected versions
        results_dict = dict(batch_results)
        for tid, corrected_result in corrected_results:
            results_dict[tid] = corrected_result
            corrections_made += 1
            correction_tokens += corrected_result.get(
                "_correction_tokens", 0)

        batch_results = list(results_dict.items())

    return batch_results, corrections_made, correction_tokens


# ── Full pipeline ───────────────────────────────────────────────────

BATCH_SIZE = 50
CHECKPOINT_EVERY = 1  # checkpoint + log every batch (= every 50 threads)


async def extract_all(
    filtered_path: Path,
    org: str,
    data_dir: Path,
    output_path: Path,
    checkpoint_path: Path,
    limit: int | None = None,
    offset: int = 0,
    thread_ids: set[str] | None = None,
    allow_empty: bool = False,
    force_restart: bool = False,
):
    """Run triple extraction on all threads."""

    # ── Group emails by thread ────────────────────────────────────────
    log.info("Grouping emails by thread from %s ...", filtered_path.name)
    threads = group_by_thread(filtered_path)
    _populate_outlook_scopes(threads)
    log.info("Total threads: %d", len(threads))

    # ── Person registry ───────────────────────────────────────────────
    email_to_person, persons_by_id = load_person_registry(org, data_dir)

    # ── Org context (built once, shared across all threads) ───────────
    org_context = build_org_context(persons_by_id)
    if org_context:
        ctx_lines = org_context.count("\n")
        log.info("Org context: %d internal people profiled", ctx_lines)

    # ── Name lookup for correction pass validation ────────────────────
    name_lookup = build_name_lookup(
        {"persons": list(persons_by_id.values())})
    log.info("Name lookup: %d name variants (for correction pass)",
             len(name_lookup))

    # ── Filter to specific thread IDs if provided ─────────────────────
    if thread_ids:
        threads = {tid: t for tid, t in threads.items()
                   if tid in thread_ids}
        log.info("Filtered to %d target threads (%d requested)",
                 len(threads), len(thread_ids))

    # ── Checkpoint ────────────────────────────────────────────────────
    completed = _load_checkpoint(checkpoint_path, force_restart=force_restart)

    # ── Preserve prior extractions for non-target threads ─────────────
    # Without this, an incremental run with `--thread-ids changed.txt`
    # would overwrite `_triples_raw.json` with ONLY the new threads,
    # wiping every previously-extracted thread. Load the existing file
    # and seed `completed` with the threads that are NOT being
    # re-processed — they pass through untouched. Target threads are
    # explicitly dropped so their stale entries are replaced by the
    # fresh extraction below.
    if thread_ids and output_path.exists():
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                preserved = {tid: v for tid, v in existing.items()
                             if tid not in thread_ids}
                # Existing entries for non-target threads get carried
                # forward; their checkpoint state stays intact.
                for tid, v in preserved.items():
                    completed.setdefault(tid, v)
                # Make sure stale checkpoint entries for TARGET threads
                # don't cause us to skip the fresh extraction.
                for tid in thread_ids:
                    completed.pop(tid, None)
                log.info(
                    "Incremental mode: preserved %d untouched threads, "
                    "invalidated %d target threads from existing %s",
                    len(preserved), len(thread_ids), output_path.name,
                )
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not pre-load %s for incremental merge: %s",
                        output_path, e)

    remaining = {tid: t for tid, t in threads.items()
                 if tid not in completed}

    if offset > 0:
        remaining = dict(list(remaining.items())[offset:])

    if limit is not None:
        remaining = dict(list(remaining.items())[:limit])

    log.info("Remaining: %d threads (checkpoint: %d done)",
             len(remaining), len(completed))

    if not remaining:
        log.info("Nothing to process — all threads already extracted")
        if completed:
            _save_output(output_path, completed)
        return completed

    # ── LLM client ────────────────────────────────────────────────────
    # OpenRouter / Gemini path — kept for reference. To reactivate,
    # set `LLM_PROVIDER=openrouter` and override `model` below with
    # `settings.openrouter_llm_model`.
    #
    # NVIDIA DeepSeek v3.2 (40 requests/minute provider cap).
    validate_for("triple_extractor")
    settings = get_settings()
    provider_name = settings.llm_provider
    if provider_name == "nvidia" and not settings.nvidia_api_key and settings.openrouter_api_key:
        provider_name = "openrouter"
        log.info("Falling back to OpenRouter provider for triple extraction")
    provider = get_chat_provider(provider_name)
    model = (get_settings().extraction_model
             if provider_name == "nvidia"
             else provider.default_model)

    # Per-provider rate limits and concurrency:
    #   nvidia    — 40 RPM hard cap (DeepSeek v3.2); throttle at 38, semaphore=4
    #   openrouter — 200 RPM free tier (Gemini Flash); throttle at 60, semaphore=8
    #   bedrock   — config-driven (bedrock_rpm/bedrock_concurrency). Conservative
    #               defaults; raise via Service Quotas. NOTE: Bedrock also caps
    #               tokens/min (TPM) — the current AsyncRateLimiter is RPM-only;
    #               TPM-aware throttling lands in P1 (see SYNC_ENGINE_DESIGN §6b).
    _PROVIDER_LIMITS: dict[str, tuple[int, int]] = {
        "nvidia":      (38, 4),
        "openrouter":  (60, 8),
        "bedrock":     (settings.bedrock_rpm, settings.bedrock_concurrency),
    }
    rpm_cap, concurrency = _PROVIDER_LIMITS.get(provider.name, (38, 4))

    # max_retries=0: our own retry loop handles failures
    # timeout=120s: sufficient for fast providers; increase for slow models
    client = provider.make_async_client(max_retries=0, timeout=120.0)

    # Bedrock throttles on requests/min AND tokens/min → token-aware limiter.
    # Other providers (NVIDIA / OpenRouter) bind on RPM only.
    if provider.name == "bedrock":
        tpm_cap = settings.bedrock_tpm
        rate_limiter = TokenAwareRateLimiter(max_rpm=rpm_cap, max_tpm=tpm_cap)
        log.info("Model: %s (provider=bedrock, %d RPM + %d TPM cap, concurrency=%d)",
                 model, rpm_cap, tpm_cap, concurrency)
    else:
        rate_limiter = AsyncRateLimiter(max_calls=rpm_cap, period=60.0)
        log.info("Model: %s (provider=%s, %d RPM cap)", model, provider.name, rpm_cap)

    semaphore = asyncio.Semaphore(concurrency)

    # ── Extraction loop ───────────────────────────────────────────────
    results = dict(completed)
    batch: list[tuple[str, dict]] = []
    batch_count = 0
    total_triples = 0
    total_tokens = 0
    total_corrections = 0
    total_correction_tokens = 0
    errors = 0
    failed_thread_ids: list[dict] = []
    start = time.time()

    for tid, thread in remaining.items():
        batch.append((tid, thread))

        if len(batch) >= BATCH_SIZE:
            batch_results, corr_count, corr_tokens = (
                await _process_batch(
                    batch, client, model, semaphore, rate_limiter,
                    email_to_person, persons_by_id,
                    org_context, name_lookup))

            for tid_r, result in batch_results:
                results[tid_r] = result
                total_triples += len(result.get("triples", []))
                total_tokens += result.get("_tokens", 0)
                if "_error" in result:
                    errors += 1
                    failed_thread_ids.append(
                        {"thread_id": tid_r,
                         "error": str(result.get("_error", "?"))[:200]})

            total_corrections += corr_count
            total_correction_tokens += corr_tokens
            batch = []
            batch_count += 1

            if batch_count % CHECKPOINT_EVERY == 0:
                _save_checkpoint(checkpoint_path, results)
                elapsed = time.time() - start
                done = len(results) - len(completed)
                log.info("  Checkpoint: %d/%d done, %d triples, "
                         "%d errors, %d corrected, %.0fs",
                         done, len(remaining), total_triples,
                         errors, total_corrections, elapsed)

    # Flush remaining batch
    if batch:
        batch_results, corr_count, corr_tokens = (
            await _process_batch(
                batch, client, model, semaphore, rate_limiter,
                email_to_person, persons_by_id,
                org_context, name_lookup))

        for tid_r, result in batch_results:
            results[tid_r] = result
            total_triples += len(result.get("triples", []))
            total_tokens += result.get("_tokens", 0)
            if "_error" in result:
                errors += 1
                failed_thread_ids.append(
                    {"thread_id": tid_r,
                     "error": str(result.get("_error", "?"))[:200]})

        total_corrections += corr_count
        total_correction_tokens += corr_tokens

    # ── Save ──────────────────────────────────────────────────────────
    _save_output(output_path, results)
    _save_checkpoint(checkpoint_path, results)

    # Persist permanently-failed thread IDs so operators can
    # investigate / re-queue them. Always written so the file exists
    # even when the run was clean (empty list = nothing to fix).
    failures_path = output_path.with_name(
        output_path.stem.replace("_triples_raw", "_extraction_failures")
        + ".json"
    )
    failures_path.write_text(
        json.dumps(failed_thread_ids, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    elapsed = time.time() - start
    processed = len(remaining)
    log.info("=" * 55)
    log.info("TRIPLE EXTRACTION COMPLETE")
    log.info("  Threads processed: %d", processed)
    log.info("  Total triples:     %d (avg %.1f/thread)",
             total_triples,
             total_triples / max(processed, 1))
    log.info("  Errors:            %d (ids → %s)",
             errors, failures_path.name)
    log.info("  Corrections:       %d threads re-extracted", total_corrections)
    log.info("  Total tokens:      %d (correction: %d)",
             total_tokens, total_correction_tokens)
    log.info("  Duration:          %.0fs", elapsed)
    log.info("  Output:            %s", output_path)
    log.info("=" * 55)

    log_summary(
        "triple_extractor",
        threads_processed=processed,
        triples_total=total_triples,
        errors=errors,
        corrections=total_corrections,
        tokens=total_tokens,
        tokens_correction=total_correction_tokens,
        model=model,
        duration_s=round(elapsed, 1),
        failures_path=str(failures_path),
    )
    # Gate: total threads in artifact must be non-empty. `results` includes
    # both newly extracted and previously checkpointed threads, so this
    # remains a valid "is there anything downstream can consume" check.
    check_nonempty("triple_extractor", count=len(results),
                   what="threads in output",
                   allow_empty=allow_empty)
    return results
