"""TagWorker — 匀速后台标签提取（每5分钟醒一次，一次batch调用）"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger


class TagWorker:
    """匀速标签提取工作线程。

    每 interval_seconds 秒醒一次，取无标签记忆（< 2个标签），
    一次 batch LLM 调用打完，写回。
    不阻塞在线查询路径。
    """

    def __init__(self, db, tag_extractor, embedding_service, tag_index, config: dict = None):
        self.db = db
        self.extractor = tag_extractor
        self.embedding = embedding_service
        self.tag_index = tag_index
        cfg = config or {}
        self.wake_interval = int(cfg.get("interval_seconds", 300))
        self.batch_size = int(cfg.get("max_batch_per_cycle", cfg.get("tag_worker_batch_size", 50)))
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.on_tags_written = None  # callback(count)

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[WaveMemory] TagWorker started")

    def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _loop(self):
        # 首次等 60s 让系统稳定
        await asyncio.sleep(60)
        while self._running:
            try:
                batch = self._fetch_untagged_batch()
                if batch:
                    await self._process_batch(batch)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[WaveMemory] TagWorker error: {e}")
            # 固定间隔休眠
            try:
                await asyncio.sleep(self.wake_interval)
            except asyncio.CancelledError:
                break
        logger.info("[WaveMemory] TagWorker stopped")

    def _fetch_untagged_batch(self) -> list:
        """获取标签不足(< 2个)的记忆。"""
        return self.db.conn.execute("""
            SELECT m.id, m.content, m.sender_name
            FROM memories m
            LEFT JOIN (
                SELECT memory_id, COUNT(*) as tag_cnt
                FROM memory_tags GROUP BY memory_id
            ) tc ON tc.memory_id = m.id
            WHERE COALESCE(tc.tag_cnt, 0) < 2
            AND m.id NOT IN (
                SELECT memory_id FROM tag_extraction_status
                WHERE status IN ('failed', 'skipped')
            )
            AND LENGTH(m.content) >= 10
            ORDER BY m.id DESC
            LIMIT ?
        """, (self.batch_size,)).fetchall()

    async def _process_batch(self, batch: list):
        """处理一批记忆。"""
        messages = [
            {"id": mem_id, "content": content, "sender": sender or "unknown"}
            for mem_id, content, sender in batch
        ]

        try:
            results = await self.extractor.extract_tags_batch(messages)

            tag_count = 0
            now = time.time()

            for i, (mem_id, content, sender) in enumerate(batch):
                tags = results[i] if i < len(results) else []
                if tags:
                    await self._save_tags(mem_id, tags)
                    tag_count += len(tags)
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at) VALUES (?, 'done', ?)",
                        (mem_id, now),
                    )
                else:
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at) VALUES (?, 'skipped', ?)",
                        (mem_id, now),
                    )
            self.db.conn.commit()

            if tag_count > 0 and self.on_tags_written:
                self.on_tags_written(tag_count)

            logger.debug(f"[WaveMemory] TagWorker batch done: {len(batch)} memories, {tag_count} tags")

        except Exception as e:
            logger.warning(f"[WaveMemory] TagWorker batch error: {e}")

    async def _save_tags(self, memory_id: int, tags: list):
        """保存标签。"""
        tag_ids = []
        for tag_info in tags:
            name = tag_info.get("name", "")
            if not name:
                continue
            tag_type = tag_info.get("type", "keyword")
            confidence = tag_info.get("confidence", 0.8)

            tag_vec = None
            try:
                tag_vec = await self.embedding.get_embedding(name)
            except Exception:
                pass

            tag_id = self.db.add_tag_extended(
                name=name,
                tag_type=tag_type,
                vector=tag_vec,
                confidence=confidence,
            )
            tag_ids.append(tag_id)

            if tag_vec is not None and self.tag_index:
                try:
                    import numpy as np
                    self.tag_index.add([tag_id], tag_vec.reshape(1, -1))
                except Exception:
                    pass

        if tag_ids:
            self.db.link_memory_tags(memory_id, tag_ids)
