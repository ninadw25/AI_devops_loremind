"""AWS Bedrock client for generating realistic Jira issue content."""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class BedrockClient:
    """Client for AWS Bedrock API calls."""
    
    region: str = "us-east-1"
    model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
    max_tokens: int = 4096
    temperature: float = 0.7
    
    _client: Any = field(default=None, repr=False)
    
    def __post_init__(self):
        """Initialize boto3 client lazily."""
        self._client = None
    
    def _get_client(self):
        """Lazy initialization of boto3 Bedrock runtime client."""
        if self._client is None:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Generate text using AWS Bedrock Claude model."""
        client = self._get_client()
        
        # Build request body for Claude
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        
        if system_prompt:
            body["system"] = system_prompt
        
        response = client.invoke_model(
            modelId=self.model_id,
            body=json.dumps(body)
        )
        
        response_body = json.loads(response["body"].read())
        content = response_body.get("content", [])
        
        if content:
            return content[0].get("text", "")
        return ""
    
    async def generate_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Async wrapper for generate."""
        return await asyncio.to_thread(
            self.generate,
            prompt,
            system_prompt,
            max_tokens,
            temperature
        )
    
    def generate_batch(
        self,
        prompts: List[str],
        system_prompt: Optional[str] = None,
        max_concurrent: int = 5,
        delay_s: float = 0.5,
    ) -> List[str]:
        """Generate multiple responses with rate limiting."""
        results = []
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def bounded_generate(prompt: str) -> str:
            async with semaphore:
                result = await self.generate_async(prompt, system_prompt)
                await asyncio.sleep(delay_s)
                return result
        
        async def run_batch():
            tasks = [bounded_generate(p) for p in prompts]
            return await asyncio.gather(*tasks)
        
        return asyncio.run(run_batch())
    
    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate JSON output, parsing the response."""
        # Add JSON formatting instruction to prompt
        enhanced_prompt = f"{prompt}\n\nRespond with valid JSON only, no additional text."
        
        try:
            response = self.generate(enhanced_prompt, system_prompt, max_tokens)
            
            # Extract JSON from response (handle markdown code blocks)
            json_str = response.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            
            return json.loads(json_str.strip())
        except json.JSONDecodeError as e:
            log.warning(f"JSON parse error: {e}, returning empty dict")
            return {}
        except Exception as e:
            log.error(f"Generation error: {e}")
            return {}
