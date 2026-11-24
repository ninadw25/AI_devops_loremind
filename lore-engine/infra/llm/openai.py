"""
infra/llm/openai.py
───────────────────
OpenAI ChatProvider (GPT-4o, GPT-4.1, etc.).

No thinking/reasoning_budget support. Streams yield ("content", text) only.
"""

from __future__ import annotations

from typing import Any, Generator

from openai import OpenAI

from core.exceptions import LLMError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


class OpenAIChatProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    @classmethod
    def from_settings(cls, s: Settings) -> "OpenAIChatProvider":
        if not s.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for llm_provider=openai")
        return cls(
            api_key=s.openai_api_key,
            base_url=s.openai_base_url,
            model=s.llm_model,
            max_tokens=s.llm_max_tokens,
            temperature=s.llm_temperature,
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
            raise LLMError(f"OpenAI chat failed: {exc}") from exc

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
            raise LLMError(f"OpenAI stream failed: {exc}") from exc
