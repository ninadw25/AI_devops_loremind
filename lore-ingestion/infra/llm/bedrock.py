"""
infra/llm/bedrock.py
────────────────────
AWS Bedrock chat provider via the OpenAI-compatible `bedrock-mantle`
Chat Completions endpoint.

Bedrock's `bedrock-mantle` endpoint speaks the OpenAI Chat Completions
protocol and authenticates with a **Bedrock API key as the bearer
token**, so it plugs straight into the existing `AsyncOpenAI` stack —
no boto3 / SigV4 path needed. (If a future model isn't available on
`bedrock-mantle`, the fallback is boto3 `bedrock-runtime` Converse,
which would be a separate provider.)

Defaults target **DeepSeek V3.2 in ap-south-1 (Mumbai)**:
  - model id   : `deepseek.v3.2`  (In-Region available; no cross-region
                 inference profile required in ap-south-1)
  - base url    : https://bedrock-mantle.ap-south-1.api.aws/v1
                 (derived from `bedrock_region` when `bedrock_base_url`
                 is empty)

`default_model` is `settings.bedrock_model` so the extraction call site
(`pipeline/extraction/extract.py`) picks it up through the standard
`provider.default_model` path.

Rate limits: Bedrock throttles on BOTH requests/min and tokens/min and
returns `ThrottlingException` (HTTP 429). This provider only constructs
the client; throttling/backoff is applied at the call site via the
shared limiter (see `scheduler/` and `pipeline/extraction/rate_limit.py`).
"""

from __future__ import annotations

from openai import AsyncOpenAI, OpenAI

from common.config import get_settings


def _resolve_base_url(base_url: str, region: str) -> str:
    """Return the configured base URL, or derive the bedrock-mantle one."""
    if base_url:
        return base_url
    return f"https://bedrock-mantle.{region}.api.aws/v1"


class BedrockChatProvider:
    """Chat provider wired to Bedrock `bedrock-mantle` (OpenAI-compatible)."""

    name = "bedrock"

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.bedrock_api_key
        self._base_url = _resolve_base_url(s.bedrock_base_url, s.bedrock_region)
        self.default_model = s.bedrock_model

    def make_sync_client(self, **kwargs) -> OpenAI:
        return OpenAI(base_url=self._base_url, api_key=self._api_key, **kwargs)

    def make_async_client(self, **kwargs) -> AsyncOpenAI:
        return AsyncOpenAI(
            base_url=self._base_url, api_key=self._api_key, **kwargs,
        )
