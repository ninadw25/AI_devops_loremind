"""
knowledge_distillation/train_modal.py
──────────────────────────────────────
Modal cloud training script for Qwen3.5-9B fine-tuning on Lore distillation data.

MODEL: unsloth/Qwen3.5-9B
  Qwen3.5 is Unsloth's unified VLM ("Causal Language Model with Vision Encoder").
  For text-only fine-tuning of this VLM, Unsloth's docs prescribe a specific
  recipe that freezes the vision tower and trains only the language layers:
    • FastVisionModel.from_pretrained(..., load_in_16bit=True)
    • FastVisionModel.get_peft_model(..., finetune_vision_layers=False)
    • UnslothVisionDataCollator as the SFTTrainer data_collator
    • SFTConfig must set remove_unused_columns=False,
      dataset_text_field="", dataset_kwargs={"skip_prepare_dataset": True}
    • Dataset messages must use vision-style content blocks:
      [{"role": "user", "content": [{"type": "text", "text": "..."}]}]
  This prevents the Qwen3VLProcessor from intercepting text as image sources
  and erroring on "Incorrect image source" / "Incorrect padding".

Usage
─────
# 1. Build dataset locally first (run once):
    ../.venv/bin/python -m knowledge_distillation.build_sft_dataset

# 2. Upload dataset splits to Modal volume:
    modal run knowledge_distillation/train_modal.py --upload

# 3. Launch training on A100-80GB:
    modal run knowledge_distillation/train_modal.py

# 4. Monitor logs:
    modal app logs lore-qwen3-finetune

# 5. Download outputs after training:
    modal volume get lore-finetune outputs/ ./kd_outputs/
"""

import logging
import time
from pathlib import Path

import modal

# ── Logging (local side) ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lore.train")


# ── Modal image ───────────────────────────────────────────────────────
# Build the container: CUDA 12.4 + PyTorch 2.5.1 + Unsloth + training stack.
#
# Install order is strict — do NOT reorder:
#   1. torch 2.5.1 (cu124) installed FIRST from the PyTorch CUDA index.
#      Must precede unsloth so pip sees CUDA torch is already satisfied
#      and does not downgrade to a CPU wheel.
#   2. unsloth + unsloth_zoo from PyPI (pre-built wheels).
#      Installing from git source triggers `Preparing metadata (pyproject.toml)`
#      which ALWAYS fails: unsloth's pyproject.toml has project.license defined
#      in two conflicting formats, which the JSON schema validator rejects
#      regardless of setuptools version. PyPI wheels skip metadata generation.
#      --extra-index-url ensures pip resolves torch deps from the CUDA index.
#   3. Training stack last (transformers v5 recommended for Qwen3 family).
#   xformers intentionally omitted — conflicts with torch._inductor.
#   uv --torch-backend=auto intentionally omitted — auto-detects no GPU in Modal's
#   CPU-only image build containers and installs CPU-only torch.

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "curl")
    .run_commands(
        "pip install --upgrade pip",
        # Step 1: PyTorch 2.5.1 CUDA 12.4 — explicit, before unsloth
        # torchvision is REQUIRED for Qwen3.5 VLM (even for text-only fine-tuning)
        "pip install torch==2.5.1 torchvision==0.20.1 "
        "--index-url https://download.pytorch.org/whl/cu124",
        # Step 2: unsloth from PyPI (wheel, no metadata generation)
        # --extra-index-url stops pip from resolving torch deps to a CPU wheel
        "pip install unsloth unsloth_zoo --extra-index-url https://download.pytorch.org/whl/cu124",
        # Step 3: training stack
        # transformers v5 is REQUIRED for Qwen3.5 (older versions will not work)
        # pillow is REQUIRED by Qwen3.5's vision processor (even when unused)
        "pip install 'transformers>=5.0.0' 'trl>=0.9.0' 'datasets>=2.19.0' "
        "'accelerate>=0.30.0' 'bitsandbytes>=0.43.0' 'peft>=0.11.0' "
        "pillow sentencepiece triton",
    )
)

app = modal.App("lore-qwen3-finetune", image=image)

# Persistent volume — dataset is uploaded here once; outputs are written here
vol = modal.Volume.from_name("lore-finetune", create_if_missing=True)
VOL_MOUNT    = "/vol"
DATASET_DIR  = f"{VOL_MOUNT}/sft_dataset"
OUTPUT_DIR   = f"{VOL_MOUNT}/outputs/qwen3-5-9b-lore"
LOG_FILE     = f"{VOL_MOUNT}/outputs/train.log"


# ── Trainer callback for step-level logging ───────────────────────────

def _make_logging_callback(logger: logging.Logger):
    """Return a TrainerCallback that logs each step and eval to the Python logger."""
    from transformers import TrainerCallback

    class StepLogger(TrainerCallback):
        def __init__(self):
            self._train_start = time.time()

        def on_train_begin(self, args, state, control, **kwargs):
            logger.info(
                f"  Training begins | steps/epoch ≈ "
                f"{state.max_steps // max(args.num_train_epochs, 1)} | "
                f"total steps: {state.max_steps}"
            )

        def on_log(self, args, state, control, logs=None, **kwargs):
            if not logs:
                return
            step       = state.global_step
            epoch      = round(state.epoch or 0, 2)
            loss       = logs.get("loss") or logs.get("train_loss")
            lr         = logs.get("learning_rate")
            eval_loss  = logs.get("eval_loss")
            grad_norm  = logs.get("grad_norm")

            parts = [f"step={step:5d}", f"epoch={epoch:.2f}"]
            if loss      is not None: parts.append(f"train_loss={loss:.4f}")
            if eval_loss is not None: parts.append(f"eval_loss={eval_loss:.4f}")
            if lr        is not None: parts.append(f"lr={lr:.2e}")
            if grad_norm is not None: parts.append(f"grad_norm={grad_norm:.3f}")

            elapsed = (time.time() - self._train_start) / 60
            parts.append(f"elapsed={elapsed:.1f}m")

            logger.info("  " + " | ".join(parts))

        def on_save(self, args, state, control, **kwargs):
            logger.info(
                f"  Checkpoint saved at step {state.global_step} → {args.output_dir}"
            )

        def on_evaluate(self, args, state, control, metrics=None, **kwargs):
            if metrics:
                logger.info(
                    f"  Eval @ step {state.global_step}: "
                    + "  ".join(f"{k}={v:.4f}" for k, v in metrics.items() if isinstance(v, float))
                )

        def on_train_end(self, args, state, control, **kwargs):
            elapsed = (time.time() - self._train_start) / 3600
            logger.info(f"  Training loop finished in {elapsed:.2f}h")

    return StepLogger()


# ── GPU memory helper ─────────────────────────────────────────────────

def _gpu_mem() -> str:
    import torch
    alloc = torch.cuda.memory_allocated()    / 1024**3
    peak  = torch.cuda.max_memory_allocated() / 1024**3
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    return f"{alloc:.1f} GB used / {total:.0f} GB total  (peak {peak:.1f} GB)"


def _section(title: str, logger: logging.Logger) -> None:
    logger.info("─" * 60)
    logger.info(f"  {title}")
    logger.info("─" * 60)


# ── Training function ─────────────────────────────────────────────────

@app.function(
    gpu="A100-80GB",
    volumes={VOL_MOUNT: vol},
    timeout=6 * 60 * 60,   # 6-hour hard cap
)
def train():
    import logging as _logging
    import json
    import torch
    from pathlib import Path as _Path
    from datasets import load_dataset
    from unsloth import FastVisionModel
    from unsloth.trainer import UnslothVisionDataCollator
    from trl import SFTTrainer, SFTConfig

    # ── Configure logging inside container ───────────────────────────
    _Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            _logging.StreamHandler(),                   # Modal live log stream
            _logging.FileHandler(LOG_FILE, mode="w"),   # Persistent copy in volume
        ],
        force=True,
    )
    L = _logging.getLogger("lore.train")

    run_start = time.time()
    L.info("=" * 60)
    L.info("  Lore Qwen3.5-9B Fine-Tune — START")
    L.info("=" * 60)
    L.info(f"  PyTorch version : {torch.__version__}")
    L.info(f"  CUDA version    : {torch.version.cuda}")
    L.info(f"  GPU             : {torch.cuda.get_device_name(0)}")
    L.info(f"  Total VRAM      : {torch.cuda.get_device_properties(0).total_memory / 1024**3:.0f} GB")
    torch.cuda.reset_peak_memory_stats()

    # ── 1. Verify dataset ─────────────────────────────────────────────
    _section("1. Dataset verification", L)
    splits_ok = True
    for split in ("train", "val", "test"):
        p = _Path(f"{DATASET_DIR}/{split}.jsonl")
        if not p.exists():
            L.error(f"  MISSING: {p}")
            splits_ok = False
        else:
            count = sum(1 for _ in open(p))
            size_mb = p.stat().st_size / 1024**2
            L.info(f"  {split:5s}: {count:>6,} records  ({size_mb:.1f} MB)  ✓")

    if not splits_ok:
        raise FileNotFoundError(
            "One or more dataset splits are missing from the volume.\n"
            "Run:  modal run knowledge_distillation/train_modal.py --upload"
        )

    # ── 2. Load model ─────────────────────────────────────────────────
    # Qwen3.5-9B is a unified VLM — must use FastVisionModel (not FastLanguageModel).
    # Returns a processor (not just a tokenizer); processor.tokenizer gives the text tokenizer.
    _section("2. Loading Qwen3.5-9B (VLM, bf16 LoRA — text-only mode)", L)
    t0 = time.time()

    model, processor = FastVisionModel.from_pretrained(
        model_name      = "unsloth/Qwen3.5-9B",
        max_seq_length  = 4096,
        load_in_4bit    = False,   # 16-bit LoRA (QLoRA not recommended for Qwen3.5)
        load_in_16bit   = True,    # bf16 LoRA
        full_finetuning = False,
    )
    tokenizer = processor.tokenizer     # text tokenizer from within the VLM processor
    L.info(f"  Loaded in {time.time() - t0:.1f}s  |  GPU mem: {_gpu_mem()}")

    # ── 3. Apply LoRA (freeze vision tower, train language layers only) ──
    _section("3. LoRA adapter  (r=16, α=32, language-only, dropout=0)", L)
    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers     = False,   # freeze ViT — we're doing text-only
        finetune_language_layers   = True,
        finetune_attention_modules = True,
        finetune_mlp_modules       = True,
        r               = 16,
        lora_alpha      = 32,
        lora_dropout    = 0,
        bias            = "none",
        target_modules  = [
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        use_gradient_checkpointing = "unsloth",
        random_state    = 42,
    )
    FastVisionModel.for_training(model)   # enable training mode
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total     = sum(p.numel() for p in model.parameters())
    L.info(f"  Trainable params: {trainable:,} / {total:,}  ({100*trainable/total:.3f}%)")
    L.info(f"  GPU mem: {_gpu_mem()}")

    # ── 4. Convert dataset to vision-style messages ────────────────────
    # UnslothVisionDataCollator requires messages with content-block lists:
    # [{"role": "...", "content": [{"type": "text", "text": "..."}]}]
    # Our dataset currently has role/content as plain strings — transform to blocks.
    _section("4. Converting conversations → vision-style messages", L)

    def to_vision_messages(examples):
        """Wrap each string content in [{type: text, text: ...}] blocks — no images."""
        out = []
        for conversations in examples["conversations"]:
            messages = [
                {
                    "role":    msg["role"],
                    "content": [{"type": "text", "text": msg["content"]}],
                }
                for msg in conversations
            ]
            out.append(messages)
        return {"messages": out}

    # ── 5. Load dataset ───────────────────────────────────────────────
    _section("5. Dataset loading", L)
    t0 = time.time()
    dataset = load_dataset(
        "json",
        data_files={
            "train":      f"{DATASET_DIR}/train.jsonl",
            "validation": f"{DATASET_DIR}/val.jsonl",
        },
    )
    L.info(f"  Raw counts — train: {len(dataset['train']):,}  val: {len(dataset['validation']):,}")

    # Convert to vision-style messages and drop the old "conversations" column.
    dataset = dataset.map(
        to_vision_messages,
        batched         = True,
        num_proc        = 4,
        remove_columns  = ["conversations"],
    )
    L.info(f"  Converted in {time.time() - t0:.1f}s")

    # Token-length histogram: render chat template via processor.tokenizer (text only)
    # then measure token counts. This is just a diagnostic; actual tokenization
    # happens inside the VisionDataCollator during training.
    sample_lengths = []
    for ex in list(dataset["train"].select(range(min(500, len(dataset["train"]))))):
        rendered = tokenizer.apply_chat_template(
            ex["messages"], tokenize=False, add_generation_prompt=False,
        )
        sample_lengths.append(len(tokenizer(rendered, add_special_tokens=True).input_ids))

    p50 = sorted(sample_lengths)[len(sample_lengths) // 2]
    p95 = sorted(sample_lengths)[int(len(sample_lengths) * 0.95)]
    p99 = sorted(sample_lengths)[int(len(sample_lengths) * 0.99)]
    L.info(f"  Token lengths (sample n=500) — p50={p50}  p95={p95}  p99={p99}  max={max(sample_lengths)}")
    if p95 > 3500:
        L.warning(f"  ⚠ p95 token length {p95} is close to max_seq_length=4096 — consider bumping max_seq_length")

    # ── 6. Trainer (vision-compatible config) ──────────────────────────
    # Mandatory for Qwen3.5 VLM text-only SFT (per Unsloth docs):
    #   remove_unused_columns = False
    #   dataset_text_field    = ""     (empty — no text field, collator handles it)
    #   dataset_kwargs        = {"skip_prepare_dataset": True}
    #   data_collator         = UnslothVisionDataCollator(model, processor)
    #   processing_class      = tokenizer (text tokenizer, NOT full processor)
    # The collator masks non-response tokens internally (equivalent to
    # train_on_responses_only); no separate call is needed.
    _section("6. SFTTrainer config (VLM text-only)", L)
    L.info("  Epochs:           2")
    L.info("  Per-device batch: 2  |  Grad accum: 8  →  effective batch: 16")
    L.info("  LR: 2e-4  |  Scheduler: cosine  |  Warmup: 50 steps")
    L.info("  Eval + save: every 250 steps  |  Best checkpoint saved")
    L.info("  Vision collator: ON  (handles response-only loss masking)")

    trainer = SFTTrainer(
        model            = model,
        processing_class = tokenizer,   # text tokenizer (not the VLM processor)
        data_collator    = UnslothVisionDataCollator(model, processor),
        train_dataset    = dataset["train"],
        eval_dataset     = dataset["validation"],
        args = SFTConfig(
            output_dir                  = OUTPUT_DIR,
            num_train_epochs            = 2,
            per_device_train_batch_size = 2,
            gradient_accumulation_steps = 8,
            learning_rate               = 2e-4,
            lr_scheduler_type           = "cosine",
            warmup_steps                = 50,
            weight_decay                = 0.01,
            max_grad_norm               = 1.0,
            bf16                        = True,
            fp16                        = False,
            logging_steps               = 25,
            save_strategy               = "steps",
            save_steps                  = 250,
            eval_strategy               = "steps",
            eval_steps                  = 250,
            load_best_model_at_end      = True,
            metric_for_best_model       = "eval_loss",
            greater_is_better           = False,
            save_total_limit            = 3,
            optim                       = "adamw_8bit",
            report_to                   = "none",
            seed                        = 42,
            # ── VLM text-only SFT (Unsloth Qwen3.5 recipe) ──
            remove_unused_columns       = False,
            dataset_text_field          = "",
            dataset_kwargs              = {"skip_prepare_dataset": True},
            max_length                  = 4096,
        ),
    )

    # Attach step-level logging callback
    trainer.add_callback(_make_logging_callback(L))
    L.info(f"  GPU mem before training: {_gpu_mem()}")

    # ── 7. Train ──────────────────────────────────────────────────────
    _section("7. Training", L)
    t0 = time.time()
    result = trainer.train()
    train_elapsed = time.time() - t0

    L.info(f"  Finished in {train_elapsed/3600:.2f}h")
    L.info(f"  Final train loss : {result.training_loss:.4f}")
    L.info(f"  Total steps      : {result.global_step}")
    L.info(f"  Samples/second   : {result.metrics.get('train_samples_per_second', '?')}")
    L.info(f"  GPU mem          : {_gpu_mem()}")

    # ── 8. Save outputs ───────────────────────────────────────────────
    # Save the full VLM processor (not just tokenizer) so inference has access
    # to the image processor + chat template, even though we don't use vision.
    _section("8. Saving outputs", L)

    lora_path   = f"{OUTPUT_DIR}/lora-adapter"
    merged_path = f"{OUTPUT_DIR}/merged-bf16"

    t0 = time.time()
    model.save_pretrained(lora_path)
    processor.save_pretrained(lora_path)
    L.info(f"  LoRA adapter  → {lora_path}  ({time.time()-t0:.1f}s)")

    t0 = time.time()
    model.save_pretrained_merged(merged_path, processor, save_method="merged_16bit")
    L.info(f"  Merged bf16   → {merged_path}  ({time.time()-t0:.1f}s)")

    # Save training summary JSON alongside the model
    summary = {
        "model":            "unsloth/Qwen3.5-9B",
        "mode":             "VLM text-only (vision tower frozen)",
        "training_loss":    result.training_loss,
        "total_steps":      result.global_step,
        "train_hours":      round(train_elapsed / 3600, 3),
        "lora_r":           16,
        "lora_alpha":       32,
        "epochs":           2,
        "effective_batch":  16,
        "max_seq_length":   4096,
        "train_samples":    len(dataset["train"]),
        "val_samples":      len(dataset["validation"]),
        "peak_gpu_mem_gb":  round(torch.cuda.max_memory_allocated() / 1024**3, 2),
    }
    import json
    summary_path = f"{OUTPUT_DIR}/training_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    L.info(f"  Summary JSON  → {summary_path}")

    # Flush volume so all files are visible from outside the container
    vol.commit()
    L.info("  Volume committed.")

    total_elapsed = time.time() - run_start
    L.info("=" * 60)
    L.info(f"  ALL DONE  |  wall time: {total_elapsed/3600:.2f}h")
    L.info(f"  Peak GPU mem: {_gpu_mem()}")
    L.info("=" * 60)
    L.info(f"  Download outputs with:")
    L.info(f"    modal volume get lore-finetune outputs/ ./kd_outputs/")


# ── Dataset upload (run once before training) ─────────────────────────

@app.function(volumes={VOL_MOUNT: vol})
def _write_splits(train_bytes: bytes, val_bytes: bytes, test_bytes: bytes) -> None:
    """Write dataset bytes into the volume from a local entrypoint."""
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        force=True,
    )
    L = _logging.getLogger("lore.upload")

    dest = Path(DATASET_DIR)
    dest.mkdir(parents=True, exist_ok=True)

    for name, data in [("train.jsonl", train_bytes), ("val.jsonl", val_bytes), ("test.jsonl", test_bytes)]:
        out = dest / name
        out.write_bytes(data)
        lines = data.count(b"\n")
        L.info(f"  Written {name}: {lines:,} lines  ({len(data)/1024**2:.1f} MB)")

    vol.commit()
    L.info("Upload complete. Volume committed.")


# ── Local entrypoints ─────────────────────────────────────────────────

@app.local_entrypoint()
def main(upload: bool = False):
    """
    modal run knowledge_distillation/train_modal.py            # train
    modal run knowledge_distillation/train_modal.py --upload   # upload dataset first
    """
    if upload:
        log.info("Uploading dataset splits to Modal volume ...")
        base = Path("knowledge_distillation/sft_dataset")
        splits = {}
        for split in ("train", "val", "test"):
            p = base / f"{split}.jsonl"
            if not p.exists():
                raise FileNotFoundError(
                    f"{p} not found. Build the dataset first:\n"
                    "  ../.venv/bin/python -m knowledge_distillation.build_sft_dataset"
                )
            data = p.read_bytes()
            lines = data.count(b"\n")
            log.info(f"  Reading {p}  ({lines:,} lines, {len(data)/1024**2:.1f} MB)")
            splits[split] = data

        _write_splits.remote(splits["train"], splits["val"], splits["test"])
        log.info("Dataset upload done.")
    else:
        log.info("Dispatching training job → Modal A100-80GB ...")
        log.info("Monitor with:  modal app logs lore-qwen3-finetune")
        train.remote()
        log.info("Training job submitted. Check Modal dashboard for status.")
