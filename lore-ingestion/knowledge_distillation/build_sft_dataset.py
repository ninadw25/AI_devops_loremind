"""
knowledge_distillation/build_sft_dataset.py
────────────────────────────────────────────
Build the SFT (supervised fine-tuning) dataset from teacher-labeled outputs.

Reads all labeled.jsonl files, filters, subsamples empty-triple threads,
anonymizes ~30% of samples, shuffles across all teachers, then writes
train/val/test JSONL splits in Unsloth ShareGPT conversation format.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m knowledge_distillation.build_sft_dataset

Output:
    knowledge_distillation/sft_dataset/train.jsonl
    knowledge_distillation/sft_dataset/val.jsonl
    knowledge_distillation/sft_dataset/test.jsonl
"""

import json
import logging
import random
import time
from pathlib import Path

from pipeline.triple_extractor import (
    SYSTEM_PROMPT,
    build_org_context,
    build_thread_prompt,
    group_by_thread,
    load_person_registry,
)

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lore.build_sft")


# ── Paths ─────────────────────────────────────────────────────────────

FILTERED_PATH   = Path("output/filtered/ezobooks_gmail_filtered.jsonl")
DATA_DIR        = Path("output")                  # load_person_registry reads DATA_DIR/knowledge/
ORG             = "ezobooks"
TEACHERS_PATH   = Path("knowledge_distillation/output")
OUTPUT_PATH     = Path("knowledge_distillation/sft_dataset")

# ── Hyper-params ──────────────────────────────────────────────────────

SEED                  = 42
EMPTY_SUBSAMPLE_RATE  = 0.20    # keep 20% of threads where triples=[]
ANON_RATE             = 0.30    # anonymize company name in 30% of samples
SPLIT_TRAIN           = 0.90
SPLIT_VAL             = 0.05
# remaining 5% → test

# ── Anonymization substitution groups ────────────────────────────────
# Each group is applied consistently within one sample so all variants
# of the real company name are replaced with the same placeholder.

ANON_GROUPS = [
    [("ezobooks", "techpeak"),    ("EZO", "TECHPEAK"),    ("Ezobooks", "Techpeak"),    ("ezobanks", "techpeak")],
    [("ezobooks", "brightforge"), ("EZO", "BRIGHTFORGE"), ("Ezobooks", "Brightforge"), ("ezobanks", "brightforge")],
    [("ezobooks", "nexaflow"),    ("EZO", "NEXAFLOW"),    ("Ezobooks", "Nexaflow"),    ("ezobanks", "nexaflow")],
    [("ezobooks", "stackrise"),   ("EZO", "STACKRISE"),   ("Ezobooks", "Stackrise"),   ("ezobanks", "stackrise")],
    [("ezobooks", "clearledger"), ("EZO", "CLEARLEDGER"), ("Ezobooks", "Clearledger"), ("ezobanks", "clearledger")],
]


def _anonymize(text: str, group: list[tuple[str, str]]) -> str:
    for src, dst in group:
        text = text.replace(src, dst)
    return text


def _section(title: str) -> None:
    log.info("─" * 56)
    log.info("  %s", title)
    log.info("─" * 56)


# ── Main ──────────────────────────────────────────────────────────────

def build() -> None:
    rng = random.Random(SEED)
    t_start = time.time()

    _section("1. Loading threads and person registry")

    if not FILTERED_PATH.exists():
        raise FileNotFoundError(f"Filtered JSONL not found: {FILTERED_PATH}")

    threads = group_by_thread(FILTERED_PATH)
    log.info("  Loaded %d threads from %s", len(threads), FILTERED_PATH)

    email_to_person, persons_by_id = load_person_registry(ORG, DATA_DIR)
    log.info("  Person registry: %d persons, %d email mappings",
             len(persons_by_id), len(email_to_person))

    org_context = build_org_context(persons_by_id)
    log.info("  Org context: %d chars", len(org_context))

    # ── Load all labeled records ──────────────────────────────────────
    _section("2. Loading teacher-labeled records")

    all_records: list[dict] = []
    teacher_dirs = sorted(TEACHERS_PATH.iterdir())

    for teacher_dir in teacher_dirs:
        if not teacher_dir.is_dir():
            continue
        labeled = teacher_dir / "labeled.jsonl"
        if not labeled.exists():
            log.warning("  No labeled.jsonl in %s — skipping", teacher_dir.name)
            continue

        teacher_records = []
        with open(labeled, encoding="utf-8") as f:
            for line in f:
                teacher_records.append(json.loads(line))

        with_t = sum(1 for r in teacher_records if r.get("triples"))
        empty  = len(teacher_records) - with_t
        log.info("  %-14s  total=%5d  with_triples=%5d  empty=%5d",
                 teacher_dir.name, len(teacher_records), with_t, empty)
        all_records.extend(teacher_records)

    log.info("  Total records loaded: %d", len(all_records))

    # ── Filter: separate with-triples vs empty ────────────────────────
    _section("3. Filtering and subsampling")

    with_triples = [r for r in all_records if r.get("triples")]
    empty_records = [r for r in all_records if not r.get("triples")]

    log.info("  With triples  : %d", len(with_triples))
    log.info("  Empty triples : %d  (will subsample %.0f%%)",
             len(empty_records), EMPTY_SUBSAMPLE_RATE * 100)

    rng.shuffle(empty_records)
    keep_empty = int(len(empty_records) * EMPTY_SUBSAMPLE_RATE)
    empty_sample = empty_records[:keep_empty]

    log.info("  Kept %d empty records after subsampling", keep_empty)

    pool = with_triples + empty_sample
    log.info("  Training pool : %d records total", len(pool))

    # ── Shuffle across all teachers ───────────────────────────────────
    # Prevents sequential teacher-identity bias in gradient updates.
    _section("4. Shuffling (seed=%d)" % SEED)
    rng.shuffle(pool)
    log.info("  Shuffled %d records across all teachers", len(pool))

    # ── Build conversation format ─────────────────────────────────────
    _section("5. Building conversation format")

    skipped_missing = 0
    skipped_prompt_error = 0
    conversations: list[dict] = []
    anon_count = 0

    for i, rec in enumerate(pool):
        tid = rec.get("thread_id")
        thread = threads.get(tid)
        if thread is None:
            skipped_missing += 1
            continue

        try:
            user_prompt = build_thread_prompt(
                thread, email_to_person, persons_by_id, org_context
            )
        except Exception as exc:
            log.warning("  build_thread_prompt failed for %s: %s", tid, exc)
            skipped_prompt_error += 1
            continue

        assistant_output = json.dumps(
            {
                "triples":         rec.get("triples", []),
                "thread_summary":  rec.get("thread_summary", ""),
                "department_tags": rec.get("department_tags", []),
                "sensitivity":     rec.get("sensitivity", "internal"),
            },
            ensure_ascii=False,
        )

        # Anonymize a random subset of samples
        if rng.random() < ANON_RATE:
            group = rng.choice(ANON_GROUPS)
            user_prompt      = _anonymize(user_prompt, group)
            assistant_output = _anonymize(assistant_output, group)
            anon_count += 1

        conversations.append({
            "conversations": [
                {"role": "system",    "content": SYSTEM_PROMPT},
                {"role": "user",      "content": user_prompt},
                {"role": "assistant", "content": assistant_output},
            ]
        })

        if (i + 1) % 2000 == 0:
            log.info("  Processed %d / %d records ...", i + 1, len(pool))

    log.info("  Built %d conversations", len(conversations))
    log.info("  Anonymized: %d (%.1f%%)", anon_count, 100 * anon_count / max(len(conversations), 1))
    if skipped_missing:
        log.warning("  Skipped %d records — thread_id not found in filtered JSONL", skipped_missing)
    if skipped_prompt_error:
        log.warning("  Skipped %d records — prompt build error", skipped_prompt_error)

    # ── Split 90 / 5 / 5 ─────────────────────────────────────────────
    _section("6. Splitting and writing output")

    n = len(conversations)
    train_end = int(n * SPLIT_TRAIN)
    val_end   = int(n * (SPLIT_TRAIN + SPLIT_VAL))

    splits = [
        ("train", conversations[:train_end]),
        ("val",   conversations[train_end:val_end]),
        ("test",  conversations[val_end:]),
    ]

    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)

    for split_name, data in splits:
        out = OUTPUT_PATH / f"{split_name}.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            for row in data:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        size_mb = out.stat().st_size / 1024**2
        log.info("  %-6s: %6d records  →  %s  (%.1f MB)",
                 split_name, len(data), out, size_mb)

    elapsed = time.time() - t_start
    log.info("─" * 56)
    log.info("  Done in %.1fs  |  Total: %d train / %d val / %d test",
             elapsed, len(splits[0][1]), len(splits[1][1]), len(splits[2][1]))
    log.info("  Upload to Modal with:")
    log.info("    modal run knowledge_distillation/train_modal.py --upload")


if __name__ == "__main__":
    build()
