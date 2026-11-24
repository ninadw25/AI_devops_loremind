"""
runners/extract_claims.py
─────────────────────────
Production claim extraction over the L0/L1 substrate (build step 7).

Orchestrates the async LLM run that fills the L3 claim tables: pull signal
conversations, build participant-context prompts from the clean registry,
extract → validate → write per conversation. Richest conversations first
(most messages), so the highest-value knowledge lands earliest and a cost cap
can stop anytime with the best claims already in.

Resumable by construction: a conversation with a `claim_digest` row is already
done and skipped — kill and restart freely (same as the fetch). Writes are per
conversation, so an interruption costs at most the handful in flight.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.extract_claims --org ezobooks --limit 200
    ../.venv/bin/python -m runners.extract_claims --org ezobooks        # full run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import defaultdict

import sqlalchemy as sa
from dotenv import load_dotenv

from builders.l0_ledger import SCHEMA, Base, Event, LedgerStore, _resolve_dsn
from builders.l3_claims import (SYSTEM_PROMPT, ThreadExtraction, build_prompt,
                                build_rows, write_conversation)
from common.logger import get_logger, setup_logging
from infra.llm import get_chat_provider

log = get_logger("runners.extract_claims")

# $/million tokens, (input, output). Only models we've actually priced —
# an unpriced model logs token counts but skips the $ estimate rather than
# guessing.
MODEL_PRICING = {
    "moonshotai.kimi-k2.5": (0.6, 3.0),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    price = MODEL_PRICING.get(model)
    if not price:
        return None
    in_rate, out_rate = price
    return prompt_tokens / 1e6 * in_rate + completion_tokens / 1e6 * out_rate


class AdaptiveLimiter:
    """AIMD concurrency limiter — the async-calls sibling of
    infra/gmail/throttle.QuotaThrottle. A fixed semaphore either wastes the
    provider's ceiling or slams into it; this self-tunes: a 429 halves the
    in-flight limit, a clean streak adds one back, converging on Bedrock's
    real capacity without hand-tuning."""

    def __init__(self, start: int, floor: int = 2, cap: int = 48, streak: int = 8):
        self.limit = max(floor, start)
        self.floor, self.cap = floor, cap
        self._streak_target = streak
        self._streak = 0
        self._active = 0
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            while self._active >= self.limit:
                await self._cond.wait()
            self._active += 1

    async def release(self) -> None:
        async with self._cond:
            self._active -= 1
            self._cond.notify_all()

    async def penalize(self) -> None:
        async with self._cond:
            self.limit = max(self.floor, self.limit // 2)
            self._streak = 0

    async def reward(self) -> None:
        async with self._cond:
            self._streak += 1
            if self._streak >= self._streak_target and self.limit < self.cap:
                self.limit += 1
                self._streak = 0
                self._cond.notify_all()


def _is_throttle(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}"
    return "429" in text or "Throttl" in text or "TooMany" in text or "RateLimit" in text


def load_registry(store: LedgerStore, org: str) -> tuple[dict, dict, dict]:
    """L1 → the lookups the prompt + resolver need.

    email_to_person : {email → person_id}
    persons_by_id   : {person_id → {canonical_name, is_internal, is_functional}}
    name_to_person  : {normalized display name → person_id}  (entity resolution)
    """
    from builders.l3_claims import norm_text
    email_to_person, persons_by_id, name_to_person = {}, {}, {}
    with store._session() as s:
        for email, pid in s.execute(sa.text(
                f"SELECT email, person_id FROM {SCHEMA}.entity_person_alias WHERE org=:o"),
                {"o": org}):
            email_to_person[email.lower()] = pid
        for pid, name, internal, stype in s.execute(sa.text(
                f"SELECT person_id, display_name, is_internal, sender_type "
                f"FROM {SCHEMA}.entity_person WHERE org=:o"), {"o": org}):
            persons_by_id[pid] = {"canonical_name": name, "is_internal": internal,
                                  "is_functional": stype == "functional_mailbox"}
            if name:
                name_to_person[norm_text(name)] = pid
        # alias display names too — a person may be named differently per source
        for email, name in s.execute(sa.text(
                f"SELECT email, display_name FROM {SCHEMA}.entity_person_alias WHERE org=:o"),
                {"o": org}):
            pid = email_to_person.get(email.lower())
            if pid and name:
                name_to_person.setdefault(norm_text(name), pid)
    return email_to_person, persons_by_id, name_to_person


def pending_conversations(store: LedgerStore, org: str, limit: int | None,
                          min_messages: int = 1) -> list[str]:
    """Signal conversations without a digest yet, SMALL multi-message first.

    Ordering is ascending by message count on purpose: "most messages" is not
    "most knowledge" — the biggest threads are largely automated notification
    pile-ups with little durable content, while 2-6 message human threads are
    where decisions and tasks live. Ascending front-loads the knowledge-dense,
    fast-to-process conversations and defers the slow mega-threads to the end.

    min_messages lets a run focus on multi-message human conversations and
    defer the single-message tier (mostly bank/GST notifications — 70% of the
    volume, lowest value/cost)."""
    with store._session() as s:
        q = sa.text(f"""
            SELECT e.conversation_id
            FROM {SCHEMA}.events e
            LEFT JOIN {SCHEMA}.claim_digest d ON d.conversation_id = e.conversation_id
            WHERE e.org = :o AND e.noise_class = 'signal'
              AND e.conversation_id IS NOT NULL AND d.conversation_id IS NULL
            GROUP BY e.conversation_id
            HAVING count(*) >= :minm
            ORDER BY count(*) ASC, e.conversation_id
            {'LIMIT :k' if limit else ''}""")
        params = {"o": org, "minm": min_messages, **({"k": limit} if limit else {})}
        return s.execute(q, params).scalars().all()


def load_conversations(store: LedgerStore, org: str, conv_ids: list[str]) -> dict:
    """conversation_id → {conversation_id, emails:[{event_id, primary_actor,
    actors, body, title, timestamp, scope_ids}]}, oldest message first."""
    with store._session() as s:
        rows = s.execute(
            sa.select(Event.conversation_id, Event.id, Event.primary_actor,
                      Event.actors, Event.body, Event.title, Event.occurred_at,
                      Event.scope_ids)
            .where(Event.org == org, Event.noise_class == "signal",
                   Event.conversation_id.in_(conv_ids))
            .order_by(Event.conversation_id, Event.occurred_at.asc().nullslast())
        ).all()
    by_conv: dict[str, list] = defaultdict(list)
    for r in rows:
        by_conv[r.conversation_id].append({
            "event_id": r.id, "primary_actor": r.primary_actor or "",
            "actors": r.actors or [], "body": r.body or "", "title": r.title or "",
            "timestamp": r.occurred_at.isoformat() if r.occurred_at else "",
            "scope_ids": r.scope_ids or [],
        })
    return {cid: {"conversation_id": cid, "emails": emails}
            for cid, emails in by_conv.items()}


async def extract_one(client, model, conv, email_to_person, persons_by_id, limiter):
    prompt = build_prompt(conv, email_to_person, persons_by_id)
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    await limiter.acquire()
    try:
        for attempt in range(3):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": prompt}],
                    temperature=0.1, max_tokens=8000)
                if resp.usage:
                    usage = {"prompt_tokens": resp.usage.prompt_tokens,
                             "completion_tokens": resp.usage.completion_tokens}
                await limiter.reward()
                text = resp.choices[0].message.content or ""
                text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
                start = text.find("{")
                if start > 0:
                    text = text[start:]
                return conv["conversation_id"], ThreadExtraction.model_validate_json(text), "", usage
            except (ValueError, json.JSONDecodeError) as exc:
                err = f"schema: {exc}"
            except Exception as exc:  # noqa: BLE001 — rate limits, transient API
                err = f"api: {exc}"
                if _is_throttle(exc):
                    await limiter.penalize()
                await asyncio.sleep(5 * (attempt + 1))
    finally:
        await limiter.release()
    return conv["conversation_id"], None, err, usage


async def run(org: str, limit: int | None, concurrency: int, batch: int,
              min_messages: int = 1, max_concurrency: int = 48) -> dict:
    load_dotenv(".env")   # explicit path: find_dotenv() crashes when detached
    setup_logging(org=org, log_dir="./output/logs", source="extract_claims")
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("extract_claims: could not connect to Postgres.")
    Base.metadata.create_all(store._engine)   # ensure L3 tables exist

    email_to_person, persons_by_id, name_to_person = load_registry(store, org)
    pending = pending_conversations(store, org, limit, min_messages)
    log.info("[claims] %d conversations pending (registry: %d persons)",
             len(pending), len(persons_by_id))
    if not pending:
        log.info("[claims] nothing to do — all signal conversations already extracted")
        store.disconnect()
        return {"conversations": 0, "claims": 0}

    provider = get_chat_provider()
    client = provider.make_async_client()
    model = provider.default_model
    limiter = AdaptiveLimiter(start=concurrency, cap=max_concurrency)
    log.info("[claims] provider=%s model=%s concurrency=%d (adaptive, cap %d)",
             provider.name, model, concurrency, max_concurrency)

    totals = {"conversations": 0, "claims": 0, "grounded": 0, "failed": 0,
              "prompt_tokens": 0, "completion_tokens": 0}
    start = time.time()
    # Process in batches so we load only `batch` conversations' bodies at a
    # time — but WRITE each conversation the moment it completes (as_completed),
    # not at the end of the batch. A batch of the biggest threads can take many
    # minutes; per-conversation writes make an interruption cost one call, not
    # the whole batch, and the digest row is the resume checkpoint.
    for i in range(0, len(pending), batch):
        chunk_ids = pending[i:i + batch]
        convs = load_conversations(store, org, chunk_ids)
        tasks = [asyncio.ensure_future(
            extract_one(client, model, convs[cid], email_to_person, persons_by_id, limiter))
            for cid in chunk_ids if cid in convs]
        for fut in asyncio.as_completed(tasks):
            cid, ext, err, usage = await fut
            totals["prompt_tokens"] += usage["prompt_tokens"]
            totals["completion_tokens"] += usage["completion_tokens"]
            if ext is None:
                totals["failed"] += 1
                log.warning("[claims] %s failed: %s", cid, err[:160])
                continue
            rows = build_rows(ext, convs[cid], org, name_to_person)
            write_conversation(store, rows)
            totals["conversations"] += 1
            totals["claims"] += len(rows["claims"])
            totals["grounded"] += sum(1 for c in rows["claims"] if c["evidence_grounded"])
            if totals["conversations"] % 25 == 0:
                elapsed = time.time() - start
                rate = totals["conversations"] / elapsed if elapsed else 0
                cost = estimate_cost(model, totals["prompt_tokens"], totals["completion_tokens"])
                cost_str = f" · ~${cost:.2f}" if cost is not None else ""
                log.info("[claims] %d/%d convs · %d claims (%d grounded) · %.2f conv/s · "
                         "inflight limit %d%s",
                         totals["conversations"], len(pending), totals["claims"],
                         totals["grounded"], rate, limiter.limit, cost_str)

    g = totals["grounded"]
    total_cost = estimate_cost(model, totals["prompt_tokens"], totals["completion_tokens"])
    log.info("[claims] tokens: %d prompt + %d completion%s", totals["prompt_tokens"],
             totals["completion_tokens"],
             f" · cost ~${total_cost:.2f}" if total_cost is not None else "")
    log.info("[claims] STAGE_SUMMARY stage=extract_claims org=%s convs=%d claims=%d "
             "grounded=%d(%.0f%%) failed=%d",
             org, totals["conversations"], totals["claims"], g,
             100 * g / max(totals["claims"], 1), totals["failed"])
    log.info("[claims] DONE %s", totals)
    store.disconnect()
    return totals


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract L3 claims from signal conversations")
    ap.add_argument("--org", required=True)
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap conversations (richest first); omit for the full run")
    ap.add_argument("--concurrency", type=int, default=12,
                    help="STARTING in-flight limit; adapts up/down from here (AIMD)")
    ap.add_argument("--max-concurrency", type=int, default=48,
                    help="Hard cap the adaptive limiter can climb to")
    ap.add_argument("--batch", type=int, default=400, help="Conversations loaded per batch")
    ap.add_argument("--min-messages", type=int, default=1,
                    help="Only conversations with >= this many messages "
                         "(2 = skip single-message notifications)")
    args = ap.parse_args()
    asyncio.run(run(args.org, args.limit, args.concurrency, args.batch,
                    args.min_messages, args.max_concurrency))


if __name__ == "__main__":
    main()
