"""
infra/llm/bedrock.py
────────────────────
AWS Bedrock ChatProvider (Anthropic Claude via the Converse API).

Auth uses a Bedrock API key (the long-term bearer token, prefix "ABSK…"),
passed through the AWS_BEARER_TOKEN_BEDROCK environment variable that
botocore reads — no AWS access-key/secret pair needed.

The Converse API differs from the OpenAI chat format in two ways this
adapter handles:
  • system messages are a separate `system=[{text}]` argument, not a role
  • message content is a list of blocks ([{"text": ...}]), not a bare string
  • roles are limited to user/assistant (consecutive same-role merged)

Claude has no OpenAI-style `reasoning_content`, so chat_stream only ever
yields ("content", text) tuples.
"""

from __future__ import annotations

import os
from typing import Any, Generator

import boto3

from core.exceptions import LLMError
from core.logging import get_logger
from core.settings import Settings

log = get_logger(__name__)


def _split_messages(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """Convert OpenAI-style messages → (system_blocks, converse_messages)."""
    system_blocks: list[dict] = []
    converse: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if content:
                system_blocks.append({"text": content})
            continue
        # Converse only allows user/assistant
        conv_role = "assistant" if role == "assistant" else "user"
        block = {"role": conv_role, "content": [{"text": content}]}
        # Merge consecutive same-role turns (Converse rejects them otherwise)
        if converse and converse[-1]["role"] == conv_role:
            converse[-1]["content"].extend(block["content"])
        else:
            converse.append(block)
    return system_blocks, converse


class BedrockChatProvider:
    name = "bedrock"

    def __init__(
        self,
        api_key: str,
        region: str,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        # botocore reads the bearer token from this env var for bedrock-runtime.
        if api_key:
            os.environ.setdefault("AWS_BEARER_TOKEN_BEDROCK", api_key)
        self._client = boto3.client("bedrock-runtime", region_name=region)

    @classmethod
    def from_settings(cls, s: Settings) -> "BedrockChatProvider":
        # No BEDROCK_API_KEY: boto3's default credential chain is used (an EC2/EKS role, AWS_PROFILE).
        return cls(
            api_key=s.bedrock_api_key,
            region=s.bedrock_region,
            model=s.llm_model,
            max_tokens=s.llm_max_tokens,
            temperature=s.llm_temperature,
        )

    def _inference_config(self, max_tokens: int | None, temperature: float | None) -> dict:
        return {
            "maxTokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
        }

    def chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **_: Any,
    ) -> str:
        system, converse = _split_messages(messages)
        try:
            kwargs: dict = {
                "modelId": self.model,
                "messages": converse,
                "inferenceConfig": self._inference_config(max_tokens, temperature),
            }
            if system:
                kwargs["system"] = system
            resp = self._client.converse(**kwargs)
            parts = resp["output"]["message"]["content"]
            return "".join(p.get("text", "") for p in parts)
        except Exception as exc:
            raise LLMError(f"Bedrock chat failed: {exc}") from exc

    def chat_stream(
        self,
        messages: list[dict],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **_: Any,
    ) -> Generator[tuple[str, str], None, None]:
        system, converse = _split_messages(messages)
        try:
            kwargs: dict = {
                "modelId": self.model,
                "messages": converse,
                "inferenceConfig": self._inference_config(max_tokens, temperature),
            }
            if system:
                kwargs["system"] = system
            resp = self._client.converse_stream(**kwargs)
            for event in resp["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                text = delta.get("text")
                if text:
                    yield ("content", text)
        except Exception as exc:
            raise LLMError(f"Bedrock stream failed: {exc}") from exc
