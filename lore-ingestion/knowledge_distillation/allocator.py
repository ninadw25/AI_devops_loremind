"""
knowledge_distillation/allocator.py
──────────────────────────────────────
Stratified allocation of filtered threads to teacher models.

Supports multiple datasets via --dataset N. Each dataset draws from
threads not yet used by any previous dataset, preserving the same
stratified interleaving (seed=42) across all datasets.

Usage:
    # Dataset 1 (nemotron, kimi, qwen — 8000 threads)
    python -m knowledge_distillation.allocator

    # Dataset 2 (minimax, nemotron_s, kimi2 — 3500 threads)
    python -m knowledge_distillation.allocator --dataset 2

    # Force regenerate a specific dataset's allocations
    python -m knowledge_distillation.allocator --dataset 2 --reallocate
"""

import argparse
import itertools
import json
import random
from datetime import datetime, timezone
from pathlib import Path

FILTERED_PATH = Path("output/filtered/ezobooks_gmail_filtered.jsonl")
ASSIGNMENTS_PATH = Path("knowledge_distillation/assignments.json")
SEED = 42

# ── Per-dataset config ────────────────────────────────────────────────
# Each dataset defines its teacher targets and round-robin pattern.
# Datasets are allocated in order; each draws only from threads unused
# by all previous datasets.

DATASET_CONFIGS: dict[int, dict] = {
    1: {
        "targets": {
            "nemotron": 2000,
            "kimi":     3000,
            "qwen":     3000,
        },
        # 2:3:3 ratio
        "round_robin": (
            ["nemotron"] * 2
            + ["kimi"]     * 3
            + ["qwen"]     * 3
        ),
    },
    2: {
        "targets": {
            "minimax":    3500,
            "nemotron_s": 3500,
            "glm":        3500,
        },
        # 1:1:1 ratio
        "round_robin": ["minimax", "nemotron_s", "glm"],
    },
    3: {
        "targets": {
            "ns3_0": 5182,
            "ns3_1": 5182,
            "ns3_2": 5181,
        },
        # 1:1:1 ratio  (13,800 unassigned + 1,745 freed from minimax = 15,545)
        "round_robin": ["ns3_0", "ns3_1", "ns3_2"],
    },
}


# ── Bucketing ─────────────────────────────────────────────────────────

def _email_count(thread: dict) -> int:
    return thread.get("email_count", len(thread.get("emails", [])))


def bucket_threads(threads: dict) -> dict[str, list[str]]:
    """Bucket all thread IDs by email count."""
    buckets: dict[str, list[str]] = {
        "tiny": [], "small": [], "medium": [], "large": [],
    }
    for tid, thread in threads.items():
        count = _email_count(thread)
        if count == 1:
            buckets["tiny"].append(tid)
        elif count <= 6:
            buckets["small"].append(tid)
        elif count <= 15:
            buckets["medium"].append(tid)
        else:
            buckets["large"].append(tid)
    return buckets


# ── Interleaving (deterministic, shared across all datasets) ──────────

def build_interleaved(buckets: dict[str, list[str]]) -> list[str]:
    """Shuffle each bucket with seed=42 then interleave round-robin.

    The resulting list is the same every run (deterministic seed).
    Datasets carve out consecutive slices of this list — dataset 1
    takes the first N threads, dataset 2 takes the next M, etc.
    """
    rng = random.Random(SEED)
    shuffled: dict[str, list[str]] = {}
    for name, tids in buckets.items():
        lst = tids.copy()
        rng.shuffle(lst)
        shuffled[name] = lst

    interleaved: list[str] = []
    bucket_iters = [
        iter(shuffled[b]) for b in ["tiny", "small", "medium", "large"]
    ]
    active = list(range(4))
    while active:
        exhausted = []
        for idx in active:
            try:
                interleaved.append(next(bucket_iters[idx]))
            except StopIteration:
                exhausted.append(idx)
        for idx in exhausted:
            active.remove(idx)

    return interleaved


# ── Allocation ────────────────────────────────────────────────────────

def allocate(
    interleaved: list[str],
    teacher_targets: dict[str, int],
    round_robin: list[str],
    skip_ids: set[str],
) -> tuple[dict[str, str], dict[str, int]]:
    """Assign threads from interleaved to teachers, skipping used IDs.

    Args:
        interleaved  : Full deterministic thread order (all 32k threads)
        teacher_targets : {teacher: quota}
        round_robin  : Cyclic assignment pattern
        skip_ids     : Thread IDs already assigned to other datasets

    Returns:
        allocations  : {thread_id: teacher_name}
        teacher_counts : {teacher_name: assigned_count}
    """
    allocations: dict[str, str] = {}
    teacher_counts: dict[str, int] = {t: 0 for t in teacher_targets}
    rr_cycle = itertools.cycle(round_robin)

    for tid in interleaved:
        if tid in skip_ids:
            continue  # already used by a previous dataset

        if all(teacher_counts[t] >= teacher_targets[t] for t in teacher_targets):
            break

        for _ in range(len(round_robin) * 4):
            teacher = next(rr_cycle)
            if teacher_counts[teacher] < teacher_targets[teacher]:
                allocations[tid] = teacher
                teacher_counts[teacher] += 1
                break

    return allocations, teacher_counts


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate stratified thread allocations for knowledge distillation"
    )
    parser.add_argument(
        "--dataset", type=int, default=1, choices=list(DATASET_CONFIGS),
        help="Dataset number to allocate (default: 1)",
    )
    parser.add_argument(
        "--reallocate", action="store_true",
        help="Force regeneration of this dataset's allocations",
    )
    parser.add_argument(
        "--input", default=str(FILTERED_PATH),
        help=f"Path to filtered JSONL (default: {FILTERED_PATH})",
    )
    args = parser.parse_args()

    dataset_num = args.dataset
    cfg = DATASET_CONFIGS[dataset_num]
    teacher_targets = cfg["targets"]
    round_robin = cfg["round_robin"]

    # ── Load or create assignments file ───────────────────────────────
    assignments_path = ASSIGNMENTS_PATH
    existing: dict = {}
    if assignments_path.exists():
        existing = json.loads(assignments_path.read_text(encoding="utf-8"))

    existing_allocations: dict[str, str] = existing.get("allocations", {})
    existing_totals: dict[str, int] = existing.get("totals", {})

    # Check if this dataset is already allocated
    dataset_teachers = set(teacher_targets.keys())
    already_done = dataset_teachers & set(existing_totals.keys())

    if already_done and not args.reallocate:
        print(f"Dataset {dataset_num} already allocated:")
        for t in sorted(already_done):
            print(f"  {t}: {existing_totals[t]:,} threads")
        print("Use --reallocate to redo this dataset's allocations.")
        return

    # If reallocating, remove this dataset's entries from existing
    if args.reallocate and already_done:
        print(f"Clearing dataset {dataset_num} allocations ...")
        existing_allocations = {
            tid: t for tid, t in existing_allocations.items()
            if t not in dataset_teachers
        }
        for t in dataset_teachers:
            existing_totals.pop(t, None)

    # ── Load threads ──────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        return

    print(f"Loading threads from {input_path} ...")
    from pipeline.triple_extractor import group_by_thread

    threads = group_by_thread(input_path)
    print(f"  Loaded {len(threads):,} threads total")

    buckets = bucket_threads(threads)
    bucket_counts = {name: len(tids) for name, tids in buckets.items()}
    print(f"  Bucket distribution: {bucket_counts}")

    # ── Build deterministic interleaved order ─────────────────────────
    interleaved = build_interleaved(buckets)

    # Threads already used by other datasets
    skip_ids: set[str] = set(existing_allocations.keys())
    available = len(interleaved) - len(skip_ids)
    print(f"  Already allocated: {len(skip_ids):,}  Available: {available:,}")

    # ── Allocate ──────────────────────────────────────────────────────
    total_target = sum(teacher_targets.values())
    print(
        f"\nAllocating dataset {dataset_num}: "
        f"{total_target} threads across {list(teacher_targets.keys())} ..."
    )

    new_allocations, new_counts = allocate(
        interleaved, teacher_targets, round_robin, skip_ids
    )

    print(f"\nDataset {dataset_num} allocation results:")
    for teacher, count in new_counts.items():
        target = teacher_targets[teacher]
        print(f"  {teacher:12s}  {count:>5,} / {target:,}")
    print(f"  total           {len(new_allocations):>5,}")

    for teacher, target in teacher_targets.items():
        got = new_counts.get(teacher, 0)
        if got < target:
            print(
                f"  WARNING: {teacher} is {target - got} short "
                f"(not enough unused threads)"
            )

    # ── Merge and save ────────────────────────────────────────────────
    merged_allocations = {**existing_allocations, **new_allocations}
    merged_totals = {**existing_totals, **new_counts}

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "total_threads_in_dataset": len(threads),
        "bucket_counts": bucket_counts,
        "allocations": merged_allocations,
        "totals": merged_totals,
    }

    assignments_path.parent.mkdir(parents=True, exist_ok=True)
    assignments_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved: {assignments_path}")
    print(f"All allocations in file: {merged_totals}")


if __name__ == "__main__":
    main()
