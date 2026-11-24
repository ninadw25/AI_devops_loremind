"""
infra/llm/openrouter.py
───────────────────────
OpenRouter ChatProvider.

OpenRouter is OpenAI-API-compatible — same interface, different base URL
and model namespace (e.g. "google/gemini-2.0-flash-001", "anthropic/claude-3-5-sonnet").
env var OPENROUTER_LLM_MODEL (maps to settings.llm_model when this provider is active).
"""

from __future__ import annotations

from typing import Any, Generator

from openai import OpenAI

from core.exceptions import LLMError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


class OpenRouterChatProvider:
    name = "openrouter"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        temperature: float,
        timeout: float,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenRouterChatProvider":
        if not s.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required for llm_provider=openrouter")
        return cls(
            api_key=s.openrouter_api_key,
            base_url=s.openrouter_base_url,
            model=s.llm_model,
            max_tokens=s.llm_max_tokens,
            temperature=s.llm_temperature,
            timeout=s.openrouter_timeout_s,
        )

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **_: Any,
    ) -> str:
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as exc:
            raise LLMError(f"OpenRouter chat failed: {exc}") from exc

    def chat_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **_: Any,
    ) -> Generator[tuple[str, str], None, None]:
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature if temperature is not None else self._temperature,
                max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
                stream=True,
            )
            for chunk in completion:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield ("content", chunk.choices[0].delta.content)
        except Exception as exc:
            raise LLMError(f"OpenRouter stream failed: {exc}") from exc
