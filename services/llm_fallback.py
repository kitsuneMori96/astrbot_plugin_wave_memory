"""WaveMemory LLM 调用客户端 — 通过 AstrBot Provider 系统调用"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("wavememory")


class LLMResponse:
    """简单的 LLM 响应包装"""
    def __init__(self, text: str):
        self.completion_text = text


class LLMFallbackClient:
    """通过 AstrBot Provider 系统调用 LLM。

    支持 provider_id fallback 链：按顺序尝试，第一个成功即返回。
    """

    def __init__(self, context, provider_ids: list[str] = None, *,
                 log_prefix: str = "[WaveMemory]", **kwargs):
        self.context = context
        self.provider_ids = [p for p in (provider_ids or []) if p]
        self.log_prefix = log_prefix

    async def text_chat(self, *, prompt: str, system_prompt: Optional[str] = None,
                        contexts: Optional[list] = None, **kwargs) -> LLMResponse:
        """通过 AstrBot provider 调用 LLM。"""
        if not self.provider_ids:
            raise RuntimeError(f"{self.log_prefix} No provider_ids configured")

        last_error = None
        for provider_id in self.provider_ids:
            try:
                provider = self.context.get_provider_by_id(provider_id)
                if not provider:
                    continue

                # 构建 prompt（AstrBot provider 的 text_chat 接口）
                full_prompt = prompt
                if system_prompt:
                    full_prompt = f"{system_prompt}\n\n{prompt}"

                response = await provider.text_chat(
                    prompt=full_prompt,
                    contexts=contexts or [],
                )

                if response and response.completion_text:
                    return LLMResponse(response.completion_text)

            except Exception as e:
                last_error = e
                logger.warning(f"{self.log_prefix} provider '{provider_id}' failed: {e}")
                continue

        if last_error:
            logger.error(f"{self.log_prefix} LLM 调用失败: {last_error}")
            raise last_error
        raise RuntimeError(f"{self.log_prefix} All providers failed")


# 辅助函数：从配置构建 provider_id 链
def parse_provider_ids(value) -> list[str]:
    """解析逗号分隔的 provider_id 字符串。"""
    if not value:
        return []
    if isinstance(value, list):
        return [v.strip() for v in value if v and v.strip()]
    return [v.strip() for v in str(value).split(",") if v.strip()]


def provider_ids_from_config(config: dict, prefix: str = "provider_") -> list[str]:
    """从配置字典中按 prefix_1, prefix_2, ... 顺序提取 provider_id 列表。"""
    if not config:
        return []
    ids = []
    for i in range(1, 10):
        key = f"{prefix}{i}"
        val = config.get(key, "")
        if val and val.strip():
            ids.append(val.strip())
    return ids


def build_provider_chain(primary: str = "", fallback=None) -> list[str]:
    """构建 provider 优先级链：primary 在前，fallback 在后。"""
    chain = []
    if primary and primary.strip():
        chain.append(primary.strip())
    if fallback:
        if isinstance(fallback, str):
            chain.extend(parse_provider_ids(fallback))
        elif isinstance(fallback, list):
            chain.extend([f for f in fallback if f and f.strip()])
    return chain
