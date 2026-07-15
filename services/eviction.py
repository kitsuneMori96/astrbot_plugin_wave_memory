"""EvictionService — 记忆淘汰服务

定期执行：
- noise: 7 天后从 DB 删除
- chat: 30 天无访问 → 从 HNSW 索引移除（DB 保留）
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex


class EvictionService:
    """记忆淘汰服务 — 定期清理低价值记忆。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        noise_ttl_days: int = 7,
        chat_stale_days: int = 30,
        eviction_interval_hours: float = 6.0,
    ):
        self.db = db
        self.memory_index = memory_index
        self.noise_ttl = noise_ttl_days * 86400
        self.chat_stale = chat_stale_days * 86400
        self.interval = eviction_interval_hours * 3600
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {"noise_deleted": 0, "chat_evicted": 0}

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[WaveMemory] EvictionService started (noise=%dd, chat=%dd)",
                    self.noise_ttl // 86400, self.chat_stale // 86400)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        """定时淘汰循环。首次延迟 10 分钟。"""
        await asyncio.sleep(600)
        while self._running:
            try:
                await self.evict_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[EvictionService] Error: {e}")
            await asyncio.sleep(self.interval)

    async def evict_once(self):
        """执行一次淘汰。"""
        # 1. noise: 删除超过 TTL 的
        noise_deleted = self.db.delete_memories_by_source("noise", self.noise_ttl)
        if noise_deleted > 0:
            self._stats["noise_deleted"] += noise_deleted
            logger.info(f"[EvictionService] Deleted {noise_deleted} noise memories (>{self.noise_ttl//86400}d)")

        # 2. chat: 长时间未访问的移出索引
        stale_ids = self.db.get_stale_memories("chat", self.chat_stale)
        evicted = 0
        for mem_id in stale_ids:
            try:
                self.memory_index.mark_deleted([mem_id])
                self.db.mark_evicted(mem_id)
                evicted += 1
            except Exception:
                pass  # 可能已不在索引中

        if evicted > 0:
            self._stats["chat_evicted"] += evicted
            self.memory_index.save()
            logger.info(f"[EvictionService] Evicted {evicted} stale chat memories from index (>{self.chat_stale//86400}d)")

    @property
    def stats(self) -> dict:
        return dict(self._stats)
