"""
infra/llm/nvidia.py
───────────────────
NVIDIA Nemotron ChatProvider.

Supports enable_thinking + reasoning_budget via extra_body.
One OpenAI-compatible client instance reused for the process lifetime.
"""

from __future__ import annotations

from typing import Any, Generator

from openai import OpenAI

from core.exceptions import LLMError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


class NvidiaChatProvider:
    name = "nvidia"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        temperature: float,
        reasoning_budget: int,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._reasoning_budget = reasoning_budget
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_settings(cls, s: Settings) -> "NvidiaChatProvider":
        if not s.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required for llm_provider=nvidia")
        return cls(
            api_key=s.nvidia_api_key,
            base_url=s.nvidia_base_url,
            model=s.llm_model,
            max_tokens=s.llm_max_tokens,
            temperature=s.llm_temperature,
            reasoning_budget=s.llm_reasoning_budget,
        )

    def _extra_body(self, enable_thinking: bool, budget: int) -> dict:
        return {
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
            "reasoning_budget": budget,
        }

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool = True,
        reasoning_budget: int | None = None,
        **_: Any,
    ) -> str:
        budget = reasoning_budget if reasoning_budget is not None else self._reasoning_budget
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                extra_body=self._extra_body(enable_thinking, budget),
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            raise LLMError(f"NVIDIA chat failed: {exc}") from exc

    def chat_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool = True,
        reasoning_budget: int | None = None,
        **_: Any,
    ) -> Generator[tuple[str, str], None, None]:
        budget = reasoning_budget if reasoning_budget is not None else self._reasoning_budget
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                extra_body=self._extra_body(enable_thinking, budget),
                stream=True,
            )
            for chunk in completion:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ("reasoning", reasoning)
                if delta.content:
                    yield ("content", delta.content)
        except Exception as exc:
            raise LLMError(f"NVIDIA stream failed: {exc}") from exc
