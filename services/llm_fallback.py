"""Unified LLM client for WaveMemory on Hermes — 直接调用 OpenAI-compatible API"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Optional

logger = logging.getLogger("wavememory")

# 默认使用 codiz.dev（和 Hermes gateway 同一个 API）
DEFAULT_BASE_URL = "http://host.docker.internal:5580/v1"
DEFAULT_API_KEY = "123456"
DEFAULT_MODEL = "claude-opus-4.6"


class LLMResponse:
    """简单的 LLM 响应包装"""
    def __init__(self, text: str):
        self.completion_text = text


class LLMFallbackClient:
    """直接调用 OpenAI-compatible API（不依赖 AstrBot context）"""

    def __init__(self, context=None, provider_ids=None, *, 
                 log_prefix: str = "[WaveMemory]",
                 base_url: str = DEFAULT_BASE_URL,
                 api_key: str = DEFAULT_API_KEY,
                 model: str = DEFAULT_MODEL):
        self.log_prefix = log_prefix
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    async def text_chat(self, *, prompt: str, system_prompt: Optional[str] = None, 
                        contexts: Optional[list] = None, **kwargs) -> LLMResponse:
        """调用 LLM API（Anthropic 格式）"""
        messages = []
        if contexts:
            for ctx in contexts:
                if isinstance(ctx, dict):
                    messages.append(ctx)
                else:
                    messages.append({"role": "user", "content": str(ctx)})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 500,
        }
        if system_prompt:
            body["system"] = system_prompt

        data = json.dumps(body).encode()

        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                text = result["content"][0]["text"]
                return LLMResponse(text)
        except Exception as e:
            logger.error(f"{self.log_prefix} LLM call failed: {e}")
            raise


# 兼容旧接口
def parse_provider_ids(value) -> list[str]:
    return []

def provider_ids_from_config(config=None, **kwargs) -> list[str]:
    return []

def build_provider_chain(primary="", fallback=None) -> list[str]:
    return []
