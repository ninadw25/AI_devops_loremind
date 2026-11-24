"""
chronograph/config.py
─────────────────────
Compatibility shim. The single source of truth for env vars is now
`common/config.py:Settings`. This module re-exports those values under
the legacy names that chronograph internals still import.

Direct env reads (`os.getenv`) have been removed. New code in
chronograph/ should import from `common.config.get_settings()` directly.
"""

from common.config import get_settings

_s = get_settings()

# ── Code Platform Credentials ──
GITLAB_PAT = _s.gitlab_pat
GITLAB_BASE_URL = _s.gitlab_base_url
GITHUB_PAT = _s.github_pat

# ── OpenRouter (LLM + Embeddings) ──
OPENROUTER_API_KEY = _s.openrouter_api_key
OPENROUTER_BASE_URL = _s.openrouter_base_url
OPENROUTER_EMBED_URL = f"{OPENROUTER_BASE_URL}/embeddings"
OPENROUTER_CHAT_URL = f"{OPENROUTER_BASE_URL}/chat/completions"
OPENROUTER_EMBED_MODEL = _s.openrouter_embed_model
OPENROUTER_LLM_MODEL = _s.openrouter_llm_model
EMBED_BATCH_SIZE = _s.embed_batch_size
EMBED_DELAY_S = _s.embed_delay_s
OPENROUTER_TIMEOUT_S = _s.openrouter_timeout_s

# ── Ingestion settings ──
DEFAULT_BACKFILL_DAYS = _s.chronograph_backfill_days
MAX_FILE_SIZE_BYTES = _s.chronograph_max_file_size
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "__pycache__", "dist", "build", ".next"}

# ── Bundled config dict (passed to pipeline + query engine) ──
CHRONOGRAPH_CONFIG = {
    "openrouter_api_key": OPENROUTER_API_KEY,
    "openrouter_base_url": OPENROUTER_BASE_URL,
    "openrouter_embed_url": OPENROUTER_EMBED_URL,
    "openrouter_chat_url": OPENROUTER_CHAT_URL,
    "openrouter_embed_model": OPENROUTER_EMBED_MODEL,
    "openrouter_llm_model": OPENROUTER_LLM_MODEL,
    "openrouter_timeout_s": OPENROUTER_TIMEOUT_S,
    "embed_batch_size": EMBED_BATCH_SIZE,
    "embed_delay_s": EMBED_DELAY_S,
    "gitlab_pat": GITLAB_PAT,
    "gitlab_base_url": GITLAB_BASE_URL,
    "github_pat": GITHUB_PAT,
    "default_backfill_days": DEFAULT_BACKFILL_DAYS,
    "max_file_size": MAX_FILE_SIZE_BYTES,
}
