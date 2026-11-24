raining Hyperparameters

  Epochs — 2

  One epoch = the model sees every training example once. With 2 epochs, the model
   trains through the full dataset twice. Too few → underfitting (model doesn't
  learn enough). Too many → overfitting (model memorizes instead of generalizing).
   For fine-tuning on a distillation dataset, 2–3 is standard.

  ---
  Batch Size — 2 (per-device)

  How many training examples are processed together in one forward+backward pass.
  Larger batches = more stable gradients but more VRAM. We use 2 because
  Qwen3.5-9B is large — a bigger batch would OOM on the A100-80GB at 4096 context
  length.

  Effective batch size = per-device batch × grad accum = 2 × 8 = 16

  ---
  Gradient Accumulation — 8

  Instead of updating weights every step, gradients are accumulated over 8
  mini-batches before doing one weight update. This simulates a batch size of 16
  without needing 16 examples in VRAM at once. It's a memory efficiency trick —
  same learning signal, less VRAM.

  ---
  Learning Rate — 0.0002 (2e-4)

  Controls how big each weight update step is. Too high → training diverges (loss
  explodes). Too low → training is slow or gets stuck. 2e-4 is on the higher end
  for LoRA fine-tuning, which is fine because LoRA only updates a small fraction
  of parameters, so larger steps are safe.

  Scheduler: cosine decay — LR starts at 2e-4, warms up over 50 steps, then
  gradually decays to near-zero by the end of training. Prevents aggressive
  updates late in training.

  ---
  Optimizer — AdamW 8-bit

  AdamW = Adam + weight decay decoupled from gradient updates. The 8-bit variant
  (from bitsandbytes) stores optimizer states in 8-bit instead of 32-bit, cutting
  optimizer memory by ~4×. Critical for fitting a 9B model's optimizer state on a
  single GPU.

  ---
  Max Steps — 0

  When set to 0, training is controlled by epochs instead of a fixed step count.
  If you set it to e.g. 500, training would stop after 500 steps regardless of
  epochs. 0 means "run for the full 2 epochs."

  ---
  Context Length — 4096

  Maximum number of tokens the model processes in one example. Emails +
  instructions that exceed this get truncated. 4096 was chosen based on the p95
  token length check in the code — if p95 of your dataset is well under 4096, very
   little data is being cut off.

  ---
  Warmup Steps — 50

  For the first 50 steps, the LR linearly ramps from 0 → 2e-4. Without warmup, the
   model starts with large random gradient updates that can destabilize early
  training. 50 steps is a short warmup — typical for fine-tuning where the model
  is already pre-trained.

  ---
  Weight Decay — 0.01

  A regularization technique. Adds a small penalty proportional to the magnitude
  of weights, nudging them toward zero during training. Prevents any single weight
   from growing too large and overfitting. 0.01 is a mild value — enough to
  regularize without fighting the learning signal.

  ---
  Save Steps / Eval Steps — 250

  Every 250 training steps:
  - Eval: the model runs on the validation set and computes eval_loss
  - Save: a checkpoint is written to the Modal volume

  load_best_model_at_end=True means after training finishes, the checkpoint with
  the lowest eval_loss is restored — not necessarily the final one.

  ---
  Seed — 42

  Controls all randomness (weight initialization, data shuffling, dropout).
  Setting a fixed seed makes runs reproducible — running with the same seed on the
   same hardware produces identical results.

  ---
  LoRA Parameters

  Rank (r) — 16

  LoRA doesn't update the full weight matrices. Instead it learns two small
  matrices A (d × r) and B (r × d) whose product approximates the weight update.
  r=16 means these matrices have 16 columns/rows. Higher rank = more expressive
  but more parameters and VRAM. 16 is a good middle ground for a 9B model.

  ---
  Alpha (α) — 32

  A scaling factor applied to the LoRA update: the effective update is scaled by
  α/r = 32/16 = 2. This controls how much the LoRA update influences the original
  weights. Setting α = 2r (double the rank) is a common convention that gives a
  stable scaling without needing to retune LR.

  ---
  Dropout — 0

  Randomly zeroes LoRA activations during training to prevent overfitting. Set to
  0 here (disabled) — Unsloth's recommendation for Qwen3.5 because the dataset is
  small enough that gradient checkpointing and weight decay handle regularization,
   and dropout at r=16 adds noise without benefit.

  ---
  Variant — lora

  Standard LoRA (as opposed to qlora which quantizes the base model to 4-bit).
  Here the base model is loaded in bf16 (load_in_16bit=True), so full-precision
  LoRA is used. QLoRA would save more VRAM but at some quality cost — not
  recommended for Qwen3.5 per Unsloth's own docs.