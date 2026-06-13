"""黑话服务 — 挖掘调度 + DB 读写 + 跨群合并 (US-4.1~4.5)"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from .statistical_filter import JargonStatisticalFilter
from .inference import JargonInferenceEngine, JargonInjector


class JargonService:
    """黑话系统主服务。"""

    def __init__(self, db: Any, llm_client: Any = None, enabled: bool = True, config: dict = None):
        self._db = db
        self._enabled = enabled
        self._config = config or {}
        self._filter = JargonStatisticalFilter()
        self._inference = JargonInferenceEngine(llm_client) if llm_client else None
        self._injector = JargonInjector(db, max_inject=int(self._config.get("max_inject", 3)))
        # 挖掘冷却: group_id -> last_mine_ts
        self._last_mine: Dict[str, float] = {}
        # 消息计数: group_id -> count since last mine
        self._msg_count: Dict[str, int] = {}

        # 从配置读取参数
        self._min_frequency = int(self._config.get("min_frequency", 5))
        self._global_threshold = int(self._config.get("global_threshold", 3))

        # 确保 jargon 表存在
        self._ensure_table()

        # 启动预热：从近 7 天 memories 重放重建词频（变相持久化，
        # 避免重启清零导致 min_frequency 永远累积不到 → 黑话学不到）
        if self._enabled:
            try:
                self._warmup_from_memories(days=7, max_rows=20000)
            except Exception as e:
                logger.debug(f"[Jargon] warmup skipped: {e}")

    def _warmup_from_memories(self, days: int = 7, max_rows: int = 20000) -> None:
        """从近 N 天 memories 重放消息，重建内存词频统计。"""
        cutoff = time.time() - days * 86400
        rows = self._db.conn.execute(
            """SELECT content, group_id, sender_id FROM memories
               WHERE timestamp >= ? AND group_id IS NOT NULL AND group_id != ''
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, max_rows),
        ).fetchall()
        n = 0
        for content, group_id, sender_id in rows:
            if content and group_id:
                self._filter.feed(content, str(group_id), str(sender_id or ""))
                n += 1
        if n:
            groups = len(self._filter._group_freq)
            logger.info(f"[Jargon] 预热完成：重放 {n} 条消息，覆盖 {groups} 个群的词频")

    def _ensure_table(self) -> None:
        """创建 jargon 表（如不存在）。"""
        try:
            self._db.conn.execute("""
                CREATE TABLE IF NOT EXISTS jargon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    meaning TEXT DEFAULT '',
                    is_jargon INTEGER DEFAULT NULL,
                    frequency INTEGER DEFAULT 1,
                    confidence REAL DEFAULT 0,
                    is_global INTEGER DEFAULT 0,
                    group_id TEXT NOT NULL,
                    contexts TEXT DEFAULT '[]',
                    created_at INTEGER,
                    updated_at INTEGER,
                    UNIQUE(group_id, word)
                )
            """)
            self._db.conn.commit()
        except Exception as e:
            logger.debug(f"[Jargon] table ensure: {e}")

    # ─── 公开接口 ───

    def feed_message(self, text: str, group_id: str, sender_id: str = "") -> None:
        """喂入一条消息（由 on_message 调用）。"""
        if not self._enabled:
            return
        self._filter.feed(text, group_id, sender_id)
        self._msg_count[group_id] = self._msg_count.get(group_id, 0) + 1

    def should_mine(self, group_id: str) -> bool:
        """判断是否应该触发挖掘（每 10 条消息 + 20s 冷却）。"""
        count = self._msg_count.get(group_id, 0)
        if count < 10:
            return False
        last = self._last_mine.get(group_id, 0)
        if time.time() - last < 20:
            return False
        return True

    async def mine(self, group_id: str) -> List[Dict]:
        """执行一次挖掘：统计筛选 + LLM 推断。返回新发现的黑话列表。"""
        if not self._enabled:
            return []

        self._msg_count[group_id] = 0
        self._last_mine[group_id] = time.time()

        # Step 1: 统计候选
        candidates = self._filter.get_candidates(group_id, min_freq=self._min_frequency, top_k=10)
        if not candidates:
            return []

        logger.info(f"[Jargon] 挖掘 {group_id}: {len(candidates)} 候选")

        results = []
        now = int(time.time())

        for cand in candidates:
            word = cand["word"]
            contexts = cand["contexts"]

            # 检查是否已存在
            existing = self._db.conn.execute(
                "SELECT id, frequency, is_jargon FROM jargon WHERE group_id = ? AND word = ?",
                (group_id, word),
            ).fetchone()

            if existing:
                # 已存在：更新频率
                self._db.conn.execute(
                    "UPDATE jargon SET frequency = ?, updated_at = ? WHERE id = ?",
                    (cand["frequency"], now, existing[0]),
                )
                continue

            # 新候选：尝试 LLM 推断
            is_jargon = None
            meaning = ""
            confidence = 0.0

            if self._inference and contexts:
                try:
                    result = await self._inference.infer(word, contexts)
                    is_jargon = result.get("is_jargon")
                    meaning = result.get("meaning", "")
                    confidence = result.get("confidence", 0.0)
                except Exception as e:
                    logger.debug(f"[Jargon] inference error for '{word}': {e}")

            # 写入 DB
            import json
            self._db.conn.execute(
                """INSERT OR IGNORE INTO jargon (word, meaning, is_jargon, frequency, confidence, group_id, contexts, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (word, meaning, is_jargon, cand["frequency"], confidence, group_id, json.dumps(contexts, ensure_ascii=False), now, now),
            )

            if is_jargon:
                results.append({"word": word, "meaning": meaning, "confidence": confidence})

        self._db.conn.commit()

        # US-4.5: 跨群检查（同词在 >= 3 群确认 → 全局）
        self._check_global_promotion()

        return results

    def get_injection(self, text: str, group_id: str) -> str:
        """获取黑话注入文本 (US-4.3)。"""
        if not self._enabled:
            return ""
        return self._injector.get_injection(text, group_id)

    def _check_global_promotion(self) -> None:
        """US-4.5: 同词在 >= N 群确认 → 自动全局化。"""
        try:
            threshold = self._global_threshold
            rows = self._db.conn.execute(
                f"""SELECT word, COUNT(DISTINCT group_id) as cnt
                   FROM jargon WHERE is_jargon = 1 AND is_global = 0
                   GROUP BY word HAVING cnt >= ?""",
                (threshold,),
            ).fetchall()
            if rows:
                now = int(time.time())
                for word, cnt in rows:
                    self._db.conn.execute(
                        "UPDATE jargon SET is_global = 1, updated_at = ? WHERE word = ? AND is_jargon = 1",
                        (now, word),
                    )
                    logger.info(f"[Jargon] 全局化: '{word}' (确认群数: {cnt})")
                self._db.conn.commit()
        except Exception as e:
            logger.debug(f"[Jargon] global promotion error: {e}")
