"""Wave Memory 异步写入服务 — 简化版：只负责写入+embedding（不打标签）"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import numpy as np

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex
from ..engine.embedding import EmbeddingService


class MessageWriter:
    """异步消息写入服务 — 简化版。

    只负责：批量 embedding → 写入 memories + 向量索引。
    不打标签（TagWorker 异步后台处理）。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: EmbeddingService,
        batch_size: int = 10,
        flush_interval: float = 30.0,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._write_count = 0
        self._save_threshold = 100

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("[WaveMemory] MessageWriter started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def enqueue(self, message_data: dict):
        """将消息放入写入队列。"""
        await self._queue.put(message_data)

    async def _run(self):
        """主循环：批量处理队列中的消息。"""
        while self._running:
            try:
                batch = []
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=self.flush_interval
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    continue

                while len(batch) < self.batch_size:
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                await self._process_batch(batch)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[WaveMemory] Writer error: {e}")
                await asyncio.sleep(2)

        self.memory_index.save()

    async def _process_batch(self, batch: list[dict]):
        """处理一批消息：embedding + 存储。"""
        texts = [item["content"] for item in batch]
        vectors = await self.embedding.get_embeddings(texts)

        for item, vec in zip(batch, vectors):
            try:
                memory_id = self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=vec,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                )

                if vec is not None:
                    self.memory_index.add([memory_id], vec.reshape(1, -1))

                self._write_count += 1

            except Exception as e:
                logger.debug(f"[WaveMemory] Single write failed: {e}")

        if self._write_count >= self._save_threshold:
            self.memory_index.save()
            self._write_count = 0
            logger.debug(f"[WaveMemory] Index saved, total: {self.memory_index.count}")
