"""EvictionService — noise cleanup plus bounded-hot-index rebalance.

定期执行：
- noise: 7 天后从 DB 删除
- chat: 到达热窗口后仅请求热索引重平衡；canonical Memory 继续留在冷层
"""


from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger

from ..domain.scope import RuntimeScope, SessionRef
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
        write_gateway=None,
        on_hot_rebalance=None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.noise_ttl = noise_ttl_days * 86400
        self.chat_stale = chat_stale_days * 86400
        self.interval = eviction_interval_hours * 3600
        self.write_gateway = write_gateway
        self.on_hot_rebalance = on_hot_rebalance
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._stats = {"noise_deleted": 0, "chat_evicted": 0, "hot_rebalance_requested": 0}

    def start(self, supervisor=None):
        self._running = True
        if supervisor is None:
            self._task = asyncio.create_task(self._loop())
        else:
            self._task = supervisor.start(
                "wave-memory:eviction", self._loop(), owner="eviction"
            )
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

    @staticmethod
    def _scope_from_row(bot_id, session_id, visibility, group_id) -> RuntimeScope | None:
        if visibility != "group" or not all(
            isinstance(value, str) and value for value in (bot_id, session_id, group_id)
        ):
            return None
        parts = session_id.split(":", 2)
        if len(parts) != 3 or parts[1] != "group" or parts[2] != group_id:
            return None
        try:
            return RuntimeScope(
                bot_id=bot_id,
                visibility="group",
                session=SessionRef(session_id, parts[0], "group", group_id),
            )
        except (TypeError, ValueError):
            return None

    def _scoped_candidates(self, *, source: str, cutoff: float) -> dict[RuntimeScope, list[int]]:
        time_predicate = (
            "timestamp<?" if source == "noise"
            else "(last_accessed IS NULL OR last_accessed<?)"
        )
        rows = self.db.conn.execute(
            f"""SELECT id, bot_id, session_id, visibility, group_id
                  FROM memories
                 WHERE source=? AND {time_predicate} AND resolution_state='resolved'
                   AND COALESCE(quarantine, 0)=0 AND memory_type='message'""",
            (source, cutoff),
        ).fetchall()
        grouped: dict[RuntimeScope, list[int]] = {}
        for memory_id, bot_id, session_id, visibility, group_id in rows:
            scope = self._scope_from_row(bot_id, session_id, visibility, group_id)
            if scope is not None:
                grouped.setdefault(scope, []).append(int(memory_id))
        return grouped

    async def evict_once(self):
        """Delete expired noise and refresh only the derived hot membership.

        ``memory_type='evicted'`` is a real lifecycle state: using it for the
        routine chat window made cold recall impossible.  The hot-index policy
        now performs that demotion during a bounded rebuild without mutating the
        canonical Memory row.
        """
        if self.write_gateway is None:
            noise_deleted = self.db.delete_memories_by_source("noise", self.noise_ttl)
            self._stats["noise_deleted"] += noise_deleted
        else:
            now = time.time()
            noise_deleted = 0
            for scope, memory_ids in self._scoped_candidates(
                source="noise", cutoff=now - self.noise_ttl
            ).items():
                changed = await self.write_gateway.mutate_memories(
                    scope=scope,
                    memory_ids=memory_ids,
                    action="delete",
                    idempotency_hint=f"eviction:noise:{int(now // self.interval)}:{scope.session.id}",
                )
                noise_deleted += len(changed)
            if noise_deleted:
                self._stats["noise_deleted"] += noise_deleted
                logger.info(
                    f"[EvictionService] Deleted {noise_deleted} scoped noise memories "
                    f"(>{self.noise_ttl//86400}d)"
                )

        callback = self.on_hot_rebalance
        if not callable(callback):
            return
        try:
            result = callback()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            logger.warning("[EvictionService] hot-index rebalance request failed: %s", exc)
            return
        self._stats["hot_rebalance_requested"] += 1

    @property
    def stats(self) -> dict:
        return dict(self._stats)
