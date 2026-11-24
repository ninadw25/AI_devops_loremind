"""
knowledge_distillation/models/teachers.py
─────────────────────────────────────────
The single source of truth for every teacher LLM configuration used
by the distillation pipeline.

Before this module existed, each teacher lived in its own ~35-line
subclass of `TeacherBase`. They differed only in five fields
(`model_id`, `api_base_url`, `api_key_env`, `rpm_cap`,
`default_concurrency`) plus one boolean knob for whether the model
supports the NVIDIA `enable_thinking` flag. That's data, not code —
the per-teacher files added no behavior, only configuration.

Everything is now one frozen `TeacherConfig` dataclass and one
`TEACHERS` dict. Adding a new teacher is one entry. The legacy
per-teacher import paths (`KimiTeacher`, `QwenTeacher`, …) survive
as thin factory shims in their original files — see
`__init__.py` for the full re-export list.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI


# OpenRouter recommends identifying the caller via these headers.
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://loremind.ai",
    "X-Title": "Loremind Knowledge Distillation",
}


@dataclass(frozen=True)
class TeacherConfig:
    """Static config for one teacher endpoint.

    Behaviorally identical to the old `TeacherBase` subclasses: same
    attribute names, same `get_api_key` / `get_client` / `has_fallback`
    / `build_call_params` methods. The `_extra_body` field lets us
    swap the only thing that actually varied between subclasses
    (whether the model supports `chat_template_kwargs.enable_thinking`)
    via a config flag instead of an override.
    """

    teacher: str                              # registry key
    model_id: str
    api_base_url: str
    api_key_env: str
    api_key_fallback_env: str | None = None
    rpm_cap: int = 38
    default_concurrency: int = 5
    daily_cap: int | None = None

    # ── Call-time params ────────────────────────────────────────────
    temperature: float = 0.3
    top_p: float = 0.95
    max_tokens: int = 8192
    # When None, no extra_body is sent. When set, sent verbatim — the
    # only currently-meaningful payload is
    # `{"chat_template_kwargs": {"enable_thinking": False}}`.
    extra_body: dict[str, Any] | None = field(
        default_factory=lambda: {
            "chat_template_kwargs": {"enable_thinking": False}
        }
    )

    # ── Methods preserved from TeacherBase ──────────────────────────

    def get_api_key(self, fallback: bool = False) -> str:
        """Resolve API key from environment, with optional fallback."""
        if fallback and self.api_key_fallback_env:
            key = os.environ.get(self.api_key_fallback_env, "")
            if key:
                return key
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(
                f"Missing required env var: {self.api_key_env}")
        return key

    def get_client(self, fallback: bool = False) -> AsyncOpenAI:
        """Create AsyncOpenAI client for this teacher's endpoint."""
        kwargs: dict[str, Any] = {
            "api_key": self.get_api_key(fallback=fallback),
            "base_url": self.api_base_url,
        }
        if "openrouter" in self.api_base_url:
            kwargs["default_headers"] = _OPENROUTER_HEADERS
        return AsyncOpenAI(**kwargs)

    def has_fallback(self) -> bool:
        """True if a fallback API key env var is configured and present."""
        return (
            self.api_key_fallback_env is not None
            and bool(os.environ.get(self.api_key_fallback_env, ""))
        )

    def build_call_params(self) -> dict[str, Any]:
        """Return kwargs for `client.chat.completions.create()`.

        Excludes `messages` — those are added by the caller.
        """
        params: dict[str, Any] = {
            "model": self.model_id,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if self.extra_body is not None:
            params["extra_body"] = self.extra_body
        return params


# ── Constants used by multiple entries ──────────────────────────────

_NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


# ── The registry ─────────────────────────────────────────────────────
#
# Effectively unlimited RPM (600) on the ns3_* rows: the natural
# ceiling is concurrency × response time, so the sliding-window rate
# limiter shouldn't be the bottleneck for those shards.
TEACHERS: dict[str, TeacherConfig] = {
    # ── Dataset 1 ────────────────────────────────────────────────────
    "nemotron": TeacherConfig(
        teacher="nemotron",
        model_id="nvidia/nemotron-3-nano-30b-a3b",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
    ),
    "kimi": TeacherConfig(
        teacher="kimi",
        model_id="moonshotai/kimi-k2-instruct",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
    ),
    "qwen": TeacherConfig(
        teacher="qwen",
        model_id="qwen/qwen3-next-80b-a3b-instruct",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
    ),
    # ── Dataset 2 ────────────────────────────────────────────────────
    "minimax": TeacherConfig(
        teacher="minimax",
        model_id="minimaxai/minimax-m2.7",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY1",
        api_key_fallback_env="NVIDIA_API_KEY",
        rpm_cap=15,
        default_concurrency=3,
        # MiniMax-M2.7 does not support chat_template_kwargs — sending
        # extra_body causes the endpoint to reject the request.
        extra_body=None,
    ),
    "nemotron_s": TeacherConfig(
        teacher="nemotron_s",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
    ),
    "glm": TeacherConfig(
        teacher="glm",
        model_id="z-ai/glm4.7",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
    ),
    # ── Dataset 3 — Nemotron-Super sharded across 3 API keys ────────
    "ns3_0": TeacherConfig(
        teacher="ns3_0",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY",
        api_key_fallback_env="NVIDIA_API_KEY1",
        rpm_cap=600,
        default_concurrency=15,
    ),
    "ns3_1": TeacherConfig(
        teacher="ns3_1",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY1",
        api_key_fallback_env="NVIDIA_API_KEY2",
        rpm_cap=600,
        default_concurrency=15,
    ),
    "ns3_2": TeacherConfig(
        teacher="ns3_2",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        api_base_url=_NVIDIA_BASE,
        api_key_env="NVIDIA_API_KEY2",
        api_key_fallback_env="NVIDIA_API_KEY3",
        rpm_cap=600,
        default_concurrency=15,
    ),
    # ── OpenRouter free-tier route (not used by distill.py today) ───
    "qwen_or": TeacherConfig(
        teacher="qwen_or",
        model_id="qwen/qwen3-next-80b-a3b-instruct:free",
        api_base_url=_OPENROUTER_BASE,
        api_key_env="OPENROUTER_API_KEY",
        api_key_fallback_env=None,
        rpm_cap=18,
        default_concurrency=3,
    ),
}


def get_teacher(name: str) -> TeacherConfig:
    """Resolve a teacher config by registry key.

    Raises ValueError for unknown names so a typo in `--teacher`
    surfaces immediately instead of silently picking a default.
    """
    try:
        return TEACHERS[name]
    except KeyError as e:
        valid = ", ".join(sorted(TEACHERS))
        raise ValueError(
            f"Unknown teacher {name!r}. Valid options: {valid}."
        ) from e
