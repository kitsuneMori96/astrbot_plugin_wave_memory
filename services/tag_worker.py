"""TagWorker — 匀速后台标签提取 + source 升级 + 向量补生 + 索引重建"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger


class TagWorker:
    """匀速标签提取工作线程。

    每 interval_seconds 秒醒一次：
    1. 取无标签记忆（< 2个标签）batch LLM 打完写回
    2. 补生 missing vector（memory / tag）
    3. 重建向量索引
    """

    def __init__(self, db, tag_extractor, embedding_service, tag_index, memory_index=None, config: dict = None, bot_keywords: set = None):
        self.db = db
        self.extractor = tag_extractor
        self.embedding = embedding_service
        self.tag_index = tag_index
        self.memory_index = memory_index
        cfg = config or {}
        self.wake_interval = int(cfg.get("interval_seconds", 300))
        self.batch_size = int(cfg.get("max_batch_per_cycle", cfg.get("tag_worker_batch_size", 100)))
        self.bot_keywords = bot_keywords or set()
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
        await asyncio.sleep(60)
        while self._running:
            try:
                batch = self._fetch_untagged_batch()
                if batch:
                    self._reserve_batch(batch)
                    await self._process_batch(batch)

                # 补生缺失向量（直接入内存索引，不再整库追加重建）
                fixed = await self._backfill_vectors()
                if fixed:
                    if self.memory_index:
                        self.memory_index.save()
                    if self.tag_index:
                        self.tag_index.save()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[WaveMemory] TagWorker error: {e}")
            try:
                await asyncio.sleep(self.wake_interval)
            except asyncio.CancelledError:
                break
        logger.info("[WaveMemory] TagWorker stopped")

    def _fetch_untagged_batch(self) -> list:
        """获取标签不足(< 2个)的记忆。

        永久排除 skipped；failed 条目在冷却期（1小时）后会被重试。
        跳过 source=noise。
        """
        cutoff = time.time() - 3600  # 1h cooldown
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
                WHERE status IN ('skipped', 'done')
                OR (status = 'pending' AND updated_at > ?)
                OR (status = 'failed' AND updated_at > ?)
            )
            AND LENGTH(m.content) >= 10
            AND COALESCE(m.source, '') != 'noise'
            ORDER BY m.id DESC
            LIMIT ?
        """, (cutoff, cutoff, self.batch_size)).fetchall()

    def _reserve_batch(self, batch: list):
        """认领本批记忆（pending），防止与 TagBackfillJob 双跑重复调用 LLM。"""
        now = time.time()
        for mem_id, _, _ in batch:
            self.db.conn.execute(
                """INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at)
                   VALUES (?, 'pending', ?)""",
                (mem_id, now),
            )
        self.db.conn.commit()

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
                    # source 升级：如果标签涉及 bot → chat 升级为 core
                    self._maybe_upgrade_source(mem_id, tags)
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
            except Exception as e:
                logger.debug(f"[WaveMemory] TagWorker embed failed for '{name}': {e}")

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
                except Exception as e:
                    logger.debug(f"[WaveMemory] TagWorker tag_index.add failed for {tag_id}: {e}")

        if tag_ids:
            self.db.link_memory_tags(memory_id, tag_ids)

    def _maybe_upgrade_source(self, memory_id: int, tags: list[dict]):
        """如果标签中包含 bot 相关词，将 chat 升级为 core。"""
        if not self.bot_keywords:
            return
        tag_names = {t.get("name", "").lower() for t in tags}
        bot_kw_lower = {kw.lower() for kw in self.bot_keywords if kw}
        if tag_names & bot_kw_lower:
            row = self.db.conn.execute(
                "SELECT source FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row and row[0] == "chat":
                self.db.update_source(memory_id, "core")
                logger.debug(f"[TagWorker] Upgraded memory {memory_id} to core (bot-related tags)")

    async def _backfill_vectors(self, batch_size: int = 50) -> int:
        """补生 memory 和 tag 的缺失向量。返回修复数量。"""
        fixed = 0

        mem_ids = self.db.get_memories_without_vector(batch_size)
        if mem_ids:
            ph = ",".join("?" * len(mem_ids))
            rows = self.db.conn.execute(
                f"SELECT id, content FROM memories WHERE id IN ({ph})", mem_ids
            ).fetchall()
            pairs = [(mid, content) for mid, content in rows if content]
            if pairs:
                texts = [content for _, content in pairs]
                vecs = await self.embedding.get_embeddings(texts)
                for (mid, _), vec in zip(pairs, vecs):
                    if vec is None:
                        continue
                    self.db.update_memory_vector(mid, vec)
                    if self.memory_index:
                        try:
                            self.memory_index.add([mid], vec.reshape(1, -1))
                        except Exception as e:
                            logger.debug(f"[WaveMemory] memory_index.add failed for {mid}: {e}")
                    fixed += 1
            self.db.conn.commit()

        tag_rows = self.db.conn.execute(
            "SELECT id, name FROM tags WHERE vector IS NULL LIMIT ?", (batch_size,)
        ).fetchall()
        pairs = [(tid, name) for tid, name in tag_rows if name]
        if pairs:
            names = [name for _, name in pairs]
            vecs = await self.embedding.get_embeddings(names)
            for (tid, name), vec in zip(pairs, vecs):
                if vec is None:
                    continue
                new_id = self.db.add_tag(name=name, vector=vec)
                if self.tag_index:
                    try:
                        self.tag_index.add([new_id], vec.reshape(1, -1))
                    except Exception as e:
                        logger.debug(f"[WaveMemory] tag_index.add failed for {name}: {e}")
                fixed += 1
            self.db.conn.commit()

        if fixed:
            logger.info(f"[WaveMemory] TagWorker backfilled {fixed} vectors")
        return fixed
