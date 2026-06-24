"""Wave Memory 异步写入服务 — 带 source 分层门控"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import numpy as np

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex
from ..engine.embedding import EmbeddingService
from .identity_safety import is_identity_contamination


# noise 判定：这些消息不入向量索引
_NOISE_MAX_LENGTH = 10


def classify_source(
    message: str,
    sender_id: str,
    bot_keywords: set[str],
    is_at_bot: bool = False,
    noise_max_length: int = _NOISE_MAX_LENGTH,
) -> str:
    """规则引擎：零 LLM 调用判断 source 分类。

    Returns: "core" / "chat" / "noise"
    """
    # 1. bot 自己发的 → core
    if sender_id == "bot":
        return "core"
    # 2. 消息含 @bot → core
    if is_at_bot:
        return "core"
    # 3. 消息含 bot 名字/别名 → core
    msg_lower = message.lower()
    if any(kw.lower() in msg_lower for kw in bot_keywords if kw):
        return "core"
    # 4. 过短 → noise
    if len(message.strip()) < noise_max_length:
        return "noise"
    # 5. 其余 → chat
    return "chat"


class MessageWriter:
    """异步消息写入服务。

    负责：批量 embedding → 按 source 分类 → 写入 memories → 按策略入索引。
    noise 不入 HNSW 索引（省内存），core/chat 入索引。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: EmbeddingService,
        bot_keywords: set[str] = None,
        noise_max_length: int = _NOISE_MAX_LENGTH,
        batch_size: int = 10,
        flush_interval: float = 30.0,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.bot_keywords = bot_keywords or set()
        self.noise_max_length = noise_max_length
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._write_count = 0
        self._save_threshold = 100

        # 统计
        self._stats = {"core": 0, "chat": 0, "noise": 0}

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
        """处理一批消息：分类 + embedding + 存储。"""
        # 分类
        for item in batch:
            if "source" not in item:
                item["source"] = classify_source(
                    message=item["content"],
                    sender_id=item.get("sender_id", ""),
                    bot_keywords=self.bot_keywords,
                    is_at_bot=item.get("is_at_bot", False),
                    noise_max_length=self.noise_max_length,
                )

        contaminated_items = [item for item in batch if is_identity_contamination(item.get("content", ""))]
        normal_batch = [item for item in batch if item not in contaminated_items]

        # noise 不需要 embedding（省 API 调用）；身份接管污染不入向量索引，写入后归档，仅保留审计。
        need_embed = [item for item in normal_batch if item["source"] != "noise"]
        noise_items = [item for item in normal_batch if item["source"] == "noise"]

        # embed 非 noise 消息
        if need_embed:
            texts = [item["content"] for item in need_embed]
            vectors = await self.embedding.get_embeddings(texts)
        else:
            vectors = []

        # 写入有向量的消息
        for item, vec in zip(need_embed, vectors):
            try:
                memory_id = self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=vec,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    source=item["source"],
                )

                if vec is not None:
                    self.memory_index.add([memory_id], vec.reshape(1, -1))

                self._write_count += 1
                self._stats[item["source"]] = self._stats.get(item["source"], 0) + 1

            except Exception as e:
                logger.debug(f"[WaveMemory] Single write failed: {e}")

        # 写入 noise（无向量，不入索引）
        for item in noise_items:
            try:
                self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=None,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    importance=0.3,  # noise 低初始重要性
                    source="noise",
                )
                self._stats["noise"] = self._stats.get("noise", 0) + 1
            except Exception:
                pass

        # 写入身份接管污染审计，但立即归档/降权，不入向量索引、不参与召回。
        for item in contaminated_items:
            try:
                memory_id = self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=None,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    importance=0.01,
                    source="identity_quarantine",
                )
                self.db.conn.execute(
                    "UPDATE memories SET memory_type='archived', summary='quarantined: transient roleplay/identity confusion' WHERE id=?",
                    (memory_id,),
                )
                self.db.conn.commit()
                self._stats["identity_quarantine"] = self._stats.get("identity_quarantine", 0) + 1
            except Exception:
                pass

        if self._write_count >= self._save_threshold:
            self.memory_index.save()
            self._write_count = 0
            logger.debug(f"[WaveMemory] Index saved, total: {self.memory_index.count}")

    @property
    def stats(self) -> dict:
        return dict(self._stats)
