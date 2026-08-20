"""Wave Memory Embedding — 通过 AstrBot 的 Embedding Provider 获取向量"""

from __future__ import annotations

from typing import Optional

import numpy as np

from astrbot.api import logger


class EmbeddingService:
    """通过 AstrBot 的 embedding provider 获取文本向量。

    AstrBot 的 embedding provider 和 chat provider 是分开的：
    - chat provider: context.get_provider_by_id(id) → text_chat()
    - embedding provider: context.get_all_embedding_providers() → get_embeddings()

    配置中的 embedding_provider_id 用于匹配 embedding provider 的 ID。
    """

    def __init__(self, context, provider_id: str, dimension: int = 1024):
        self.context = context
        self.provider_id = provider_id
        self.dimension = dimension
        self._provider = None
        self._failed_provider_ids: set[str] = set()

    def _get_provider(self):
        """获取 embedding provider 实例。

        - 指定了 provider_id 时按 ID 匹配；
        - 留空时按顺序选择第一个「可用」的 provider（跳过连接失败的）。
        选择结果缓存；某次调用失败会在 get_embeddings 里降级切换。
        """
        if self._provider is not None:
            return self._provider

        providers = self.context.get_all_embedding_providers()
        if not providers:
            logger.warning("[WaveMemory] No embedding providers available")
            return None

        # 按 ID 匹配
        if self.provider_id:
            for p in providers:
                if hasattr(p, 'meta') and p.meta().id == self.provider_id:
                    self._provider = p
                    return p
                # fallback: 直接比较
                pid = getattr(p, 'provider_id', '') or (p.meta().id if hasattr(p, 'meta') else '')
                if pid == self.provider_id:
                    self._provider = p
                    return p

        # 留空：选第一个未被标记为失败、且未被禁用的 provider
        for p in providers:
            if self._is_disabled(p) or self._is_failed(p):
                continue
            self._provider = p
            return self._provider

        # 全部失败/禁用过：重置失败标记（不禁用标记），重新从第一个尝试
        self._failed_provider_ids.clear()
        for p in providers:
            if self._is_disabled(p):
                continue
            self._provider = p
            return self._provider

        return None

    @staticmethod
    def _is_disabled(p) -> bool:
        """provider 是否在 AstrBot 配置中被禁用（enable=false）。"""
        try:
            cfg = getattr(p, "provider_config", None) or {}
            if not cfg.get("enable", True):
                logger.debug(f"[WaveMemory] Skipping disabled embedding provider: {cfg.get('id', '')}")
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _provider_key(p) -> str:
        """provider 的唯一标识。"""
        if hasattr(p, 'meta'):
            try:
                return str(p.meta().id)
            except Exception:
                pass
        return str(getattr(p, 'provider_id', '') or getattr(p, 'id', '') or id(p))

    def _is_failed(self, p) -> bool:
        """判断 provider 是否曾被标记为连接失败。"""
        key = self._provider_key(p)
        if key in self._failed_provider_ids:
            logger.debug(f"[WaveMemory] Skipping failed embedding provider: {key}")
            return True
        return False

    def _mark_failed(self, p) -> None:
        """标记 provider 连接失败，后续选择时跳过。"""
        if p is None:
            return
        key = self._provider_key(p)
        if key:
            self._failed_provider_ids.add(key)
            logger.warning(f"[WaveMemory] Marked embedding provider failed: {key}")

    async def is_available(self) -> bool:
        """检查 embedding provider 是否可用。"""
        return self._get_provider() is not None

    async def get_embedding(self, text: str) -> Optional[np.ndarray]:
        """获取单条文本的 embedding 向量。"""
        result = await self.get_embeddings([text])
        return result[0] if result else None

    async def get_embeddings(self, texts: list[str]) -> list[Optional[np.ndarray]]:
        """批量获取 embedding 向量。"""
        if not texts:
            return []

        provider = self._get_provider()
        if not provider:
            return [None] * len(texts)

        try:
            # AstrBot embedding provider 的标准接口
            raw_result = await provider.get_embeddings(texts)
            return self._build_results(raw_result, len(texts))

        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                # Provider 的 event loop 已关闭（热重载/重启残留），清缓存重试一次
                logger.warning(f"[WaveMemory] Embedding provider event loop closed, refreshing...")
                self._provider = None
                provider = self._get_provider()
                if provider:
                    try:
                        raw_result = await provider.get_embeddings(texts)
                        return self._build_results(raw_result, len(texts))
                    except Exception as e2:
                        logger.error(f"[WaveMemory] Embedding retry failed: {e2}")
            else:
                logger.error(f"[WaveMemory] Embedding failed: {e}")
            return [None] * len(texts)

        except Exception as e:
            logger.error(f"[WaveMemory] Embedding failed: {e}")
            # 连接失败：标记当前 provider，清缓存降级到下一个可用 provider
            self._mark_failed(provider)
            self._provider = None
            fallback = self._get_provider()
            if fallback is not None and fallback is not provider:
                try:
                    logger.info(f"[WaveMemory] Falling back to embedding provider: {self._provider_key(fallback)}")
                    raw_result = await fallback.get_embeddings(texts)
                    return self._build_results(raw_result, len(texts))
                except Exception as e2:
                    logger.error(f"[WaveMemory] Embedding fallback failed: {e2}")
            return [None] * len(texts)

    @staticmethod
    def _build_results(raw_result, expected_count: int) -> list[Optional[np.ndarray]]:
        """规范化 provider 返回：过滤空/非有限/维度不一致的向量，保证长度对齐。"""
        results = []
        expected_dim = None
        for vec in raw_result:
            if vec is not None and len(vec) > 0:
                try:
                    arr = np.array(vec, dtype=np.float32)
                except Exception:
                    arr = None
                if arr is not None and arr.ndim == 1 and arr.shape[0] > 0 and np.all(np.isfinite(arr)):
                    if expected_dim is None:
                        expected_dim = arr.shape[0]
                    if arr.shape[0] == expected_dim:
                        results.append(arr)
                        continue
                    logger.warning(
                        f"[WaveMemory] Dropping embedding with dim {arr.shape[0]} != {expected_dim}"
                    )
                results.append(None)
            else:
                results.append(None)
        # 对齐输入长度（provider 可能缺行）
        while len(results) < expected_count:
            results.append(None)
        return results[:expected_count]
