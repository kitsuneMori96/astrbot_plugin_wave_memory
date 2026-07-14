"""TagWorker — 匀速后台标签提取 + source 升级判断"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from astrbot.api import logger

try:
    from ..domain.scope import RuntimeScope, ScopeValidationError, SessionRef
except ImportError:  # pragma: no cover - repository tests import top-level packages
    from domain.scope import RuntimeScope, ScopeValidationError, SessionRef


@dataclass(frozen=True)
class TagWorkItem:
    """已由 memories v2 字段验证并携带完整 RuntimeScope 的标签任务。"""

    memory_id: int
    content: str
    sender_name: str | None
    scope: RuntimeScope


class TagWorker:
    """匀速标签提取工作线程。

    每 interval_seconds 秒醒一次，取无标签记忆（< 2个标签），
    一次 batch LLM 调用打完，写回。
    打完标签后检查是否应将 chat → core（bot 相关标签升级）。
    """

    def __init__(
        self,
        db,
        tag_extractor,
        embedding_service,
        tag_index,
        config: dict = None,
        bot_keywords: set = None,
        write_gateway=None,
    ):
        self.db = db
        self.extractor = tag_extractor
        # 保留注入签名兼容；scoped_tags 不存向量，正式路径不会写 legacy tag_index。
        self.embedding = embedding_service
        self.tag_index = tag_index
        cfg = config or {}
        self.wake_interval = int(cfg.get("interval_seconds", 300))
        self.batch_size = int(cfg.get("max_batch_per_cycle", cfg.get("tag_worker_batch_size", 100)))
        self.bot_keywords = bot_keywords or set()
        self.write_gateway = write_gateway
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.on_tags_written = None  # callback(count)

    def start(self, supervisor=None):
        if self._running:
            return
        self._running = True
        if supervisor is None:
            self._task = asyncio.create_task(self._loop())
        else:
            self._task = supervisor.start(
                "wave-memory:tag-worker", self._loop(), owner="tag-worker"
            )
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

    def _fetch_untagged_batch(self) -> list[TagWorkItem]:
        """仅扫描可证明归属的 v2 group memory，并附带其完整 RuntimeScope。

        ``tag_extraction_status`` 只是 worker 进度，不是标签的正式数据面；此处绝不
        读取或写入 legacy ``tags`` / ``memory_tags``。SQL 先排除 unresolved、隔离和
        非 group 行，再在 Python 中复核 canonical SessionRef，拒绝任何不能精确证明
        ``conversation_id == group_id`` 的记录。
        """
        rows = self.db.conn.execute("""
            SELECT m.id, m.content, m.sender_name, m.group_id, m.bot_id, m.session_id, m.visibility
            FROM memories m
            LEFT JOIN scoped_memory_tags smt
              ON smt.memory_id = m.id
             AND smt.bot_id = m.bot_id
             AND smt.session_id = m.session_id
             AND smt.visibility = m.visibility
            WHERE m.resolution_state = 'resolved'
              AND COALESCE(m.quarantine, 0) = 0
              AND m.visibility = 'group'
              AND m.group_id IS NOT NULL AND m.group_id != ''
              AND m.bot_id IS NOT NULL AND m.bot_id != ''
              AND m.session_id IS NOT NULL AND m.session_id != ''
              AND m.id NOT IN (
                  SELECT memory_id FROM tag_extraction_status
                  WHERE status IN ('failed', 'skipped')
              )
              AND LENGTH(m.content) >= 10
              AND COALESCE(m.source, '') != 'noise'
            GROUP BY m.id
            HAVING COUNT(smt.tag_id) < 2
            ORDER BY m.id DESC
            LIMIT ?
        """, (self.batch_size,)).fetchall()

        batch: list[TagWorkItem] = []
        rejected_ids: list[int] = []
        for memory_id, content, sender_name, group_id, bot_id, session_id, visibility in rows:
            try:
                scope = self._scope_for_memory(
                    group_id=group_id,
                    bot_id=bot_id,
                    session_id=session_id,
                    visibility=visibility,
                )
            except (ScopeValidationError, TypeError, ValueError) as error:
                # 不从 group_id 推断平台或会话；无法构造 canonical scope 时只记录进度。
                rejected_ids.append(memory_id)
                logger.warning(
                    "[WaveMemory] TagWorker skipped memory %s: invalid RuntimeScope (%s)",
                    memory_id,
                    error,
                )
                continue
            batch.append(TagWorkItem(memory_id, content, sender_name, scope))

        if rejected_ids:
            now = time.time()
            self.db.conn.executemany(
                "INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at) "
                "VALUES (?, 'skipped', ?)",
                [(memory_id, now) for memory_id in rejected_ids],
            )
            self.db.conn.commit()
        return batch

    @staticmethod
    def _scope_for_memory(*, group_id, bot_id, session_id, visibility) -> RuntimeScope:
        """从已持久化的 v2 字段重建 scope；不接受任何 legacy 推断。"""
        if visibility != "group":
            raise ValueError("memory visibility is not group")
        if not isinstance(group_id, str) or not group_id or group_id != group_id.strip():
            raise ValueError("memory group_id is incomplete")
        if not isinstance(session_id, str):
            raise ValueError("memory session_id is incomplete")
        parts = session_id.split(":", 2)
        if len(parts) != 3:
            raise ValueError("memory session_id is not canonical")
        platform_id, session_kind, conversation_id = parts
        if session_kind != "group" or conversation_id != group_id:
            raise ValueError("memory session does not canonically identify its group")
        return RuntimeScope(
            bot_id=bot_id,
            visibility="group",
            session=SessionRef(
                id=session_id,
                platform_id=platform_id,
                kind=session_kind,
                conversation_id=conversation_id,
            ),
        )

    async def _process_batch(self, batch: list[TagWorkItem]):
        """处理一批已验证 Scope 的记忆。"""
        messages = [
            {"id": item.memory_id, "content": item.content, "sender": item.sender_name or "unknown"}
            for item in batch
        ]

        try:
            results = await self.extractor.extract_tags_batch(messages)

            tag_count = 0
            now = time.time()

            for i, item in enumerate(batch):
                tags = results[i] if i < len(results) else []
                if self.write_gateway is not None:
                    saved_count = await self.write_gateway.apply_tag_extraction(
                        scope=item.scope,
                        memory_id=item.memory_id,
                        tags=tags,
                        status="done" if tags else "skipped",
                        upgrade_source=self._should_upgrade_source(tags),
                    )
                    tag_count += saved_count
                    continue
                if tags:
                    saved_count = await self._save_tags(item, tags)
                    tag_count += saved_count
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at) VALUES (?, 'done', ?)",
                        (item.memory_id, now),
                    )
                    # 未注入协调入口的兼容测试路径仍保持原有事务语义。
                    self._maybe_upgrade_source(item, tags)
                else:
                    self.db.conn.execute(
                        "INSERT OR REPLACE INTO tag_extraction_status (memory_id, status, updated_at) VALUES (?, 'skipped', ?)",
                        (item.memory_id, now),
                    )
            if self.write_gateway is None:
                self.db.conn.commit()

            if tag_count > 0 and self.on_tags_written:
                self.on_tags_written(tag_count)

            logger.debug(f"[WaveMemory] TagWorker batch done: {len(batch)} memories, {tag_count} tags")

        except Exception as e:
            # 任一步写入失败都必须回滚，否则共享连接会一直处于 active
            # transaction，后续学习中心等写接口将被 SQLite 拒绝。
            if self.write_gateway is None:
                try:
                    self.db.conn.rollback()
                except Exception as rollback_error:
                    logger.warning(f"[WaveMemory] TagWorker rollback failed: {rollback_error}")
            logger.warning(f"[WaveMemory] TagWorker batch error: {e}")

    async def _save_tags(self, item: TagWorkItem, tags: list) -> int:
        """仅将标签和关联写入 item 自己的 scoped 数据面。"""
        saved_count = 0
        for position, tag_info in enumerate(tags, 1):
            if not isinstance(tag_info, dict):
                continue
            name = tag_info.get("name", "")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            tag_type = tag_info.get("type", "keyword")
            confidence = tag_info.get("confidence", 0.8)
            if not isinstance(tag_type, str):
                tag_type = "keyword"

            # scoped_tags 没有向量列。不得调用 add_tag_extended，也不得写 legacy tag_index。
            tag_id = self.db.upsert_scoped_tag(
                item.scope,
                name=name,
                tag_type=tag_type,
                confidence=confidence,
                metadata={"producer": "tag_worker", "memory_id": item.memory_id},
            )
            self.db.link_scoped_memory_tag(
                item.scope,
                memory_id=item.memory_id,
                tag_id=tag_id,
                position=position,
            )
            saved_count += 1
        return saved_count

    def _should_upgrade_source(self, tags: list[dict]) -> bool:
        if not self.bot_keywords:
            return False
        tag_names = {t.get("name", "").lower() for t in tags if isinstance(t, dict)}
        bot_kw_lower = {kw.lower() for kw in self.bot_keywords if kw}
        return bool(tag_names & bot_kw_lower)

    def _maybe_upgrade_source(self, item: TagWorkItem, tags: list[dict]):
        """如果标签中包含 bot 相关词，将同 Scope 的 chat 记忆升级为 core。"""
        if not self._should_upgrade_source(tags):
            return
        scope = item.scope
        assert scope.session is not None
        cursor = self.db.conn.execute(
            """UPDATE memories SET source='core'
                 WHERE id=? AND group_id=? AND bot_id=? AND session_id=? AND visibility='group'
                   AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0 AND source='chat'""",
            (item.memory_id, scope.session.conversation_id, scope.bot_id, scope.session.id),
        )
        if cursor.rowcount:
            logger.debug(f"[TagWorker] Upgraded memory {item.memory_id} to core (bot-related tags)")
