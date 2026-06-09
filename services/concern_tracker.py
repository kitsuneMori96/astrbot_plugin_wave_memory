"""ConcernTracker — 关切系统

维护 bot 当前在意的事情列表（动态、有时效）。
影响主动插话判断：群里聊到正在关注的事时更倾向参与。
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from astrbot.api import logger

from ..engine.database import WaveMemoryDB


class Concern:
    """一条关切。"""
    __slots__ = ("topic", "intensity", "origin_memory_id", "bot_id", "created_at", "last_triggered", "decay_rate")

    def __init__(self, topic: str, intensity: float = 0.7, origin_memory_id: int = 0,
                 bot_id: str = "", created_at: float = 0, last_triggered: float = 0, decay_rate: float = 0.9):
        self.topic = topic
        self.intensity = intensity
        self.origin_memory_id = origin_memory_id
        self.bot_id = bot_id
        self.created_at = created_at or time.time()
        self.last_triggered = last_triggered or time.time()
        self.decay_rate = decay_rate


class ConcernTracker:
    """关切追踪器 — 维护 bot 当前在意什么。"""

    def __init__(self, db: WaveMemoryDB, bot_id: str = "", max_concerns: int = 10):
        self.db = db
        self.bot_id = bot_id
        self.max_concerns = max_concerns
        self.concerns: list[Concern] = []
        self._ensure_table()
        self._load()

    def _ensure_table(self):
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS concerns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    intensity REAL DEFAULT 0.7,
                    bot_id TEXT DEFAULT '',
                    origin_memory_id INTEGER DEFAULT 0,
                    created_at REAL,
                    last_triggered REAL
                )
            """)
            self.db.conn.commit()
        except Exception:
            pass

    def _load(self):
        """启动时从 DB 恢复。"""
        try:
            rows = self.db.conn.execute(
                "SELECT topic, intensity, origin_memory_id, bot_id, created_at, last_triggered "
                "FROM concerns WHERE bot_id = ? ORDER BY intensity DESC LIMIT ?",
                (self.bot_id, self.max_concerns),
            ).fetchall()
            for r in rows:
                self.concerns.append(Concern(
                    topic=r[0], intensity=r[1], origin_memory_id=r[2],
                    bot_id=r[3], created_at=r[4], last_triggered=r[5],
                ))
        except Exception:
            pass

    def _persist(self):
        """持久化到 DB（全量覆写）。"""
        try:
            self.db.conn.execute("DELETE FROM concerns WHERE bot_id = ?", (self.bot_id,))
            for c in self.concerns:
                self.db.conn.execute(
                    "INSERT INTO concerns (topic, intensity, bot_id, origin_memory_id, created_at, last_triggered) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (c.topic, c.intensity, c.bot_id, c.origin_memory_id, c.created_at, c.last_triggered),
                )
            self.db.conn.commit()
        except Exception as e:
            logger.debug(f"[ConcernTracker] Persist failed: {e}")

    def add(self, topic: str, origin_memory_id: int = 0, intensity: float = 0.7):
        """新增关切。相似主题则强化。"""
        # 检查是否已有相似主题
        for c in self.concerns:
            if self._is_similar(c.topic, topic):
                c.intensity = min(1.0, c.intensity + 0.3)
                c.last_triggered = time.time()
                self._persist()
                return

        # 容量满了淘汰最弱的
        if len(self.concerns) >= self.max_concerns:
            self.concerns.sort(key=lambda c: c.intensity)
            self.concerns.pop(0)

        self.concerns.append(Concern(
            topic=topic, intensity=intensity,
            origin_memory_id=origin_memory_id, bot_id=self.bot_id,
        ))
        self._persist()
        logger.debug(f"[ConcernTracker] New concern: {topic} (intensity={intensity:.2f})")

    def tick(self):
        """衰减 + 清理。应每小时调用一次。"""
        now = time.time()
        for c in self.concerns:
            hours_elapsed = (now - c.last_triggered) / 3600
            if hours_elapsed > 0:
                c.intensity *= c.decay_rate ** hours_elapsed
                c.last_triggered = now  # 重置基准避免重复衰减

        before = len(self.concerns)
        self.concerns = [c for c in self.concerns if c.intensity > 0.1]
        if len(self.concerns) != before:
            self._persist()

    def match(self, message: str) -> float:
        """返回消息与当前关切的最高匹配度（0-1）。"""
        if not self.concerns:
            return 0.0

        msg_lower = message.lower()
        max_score = 0.0
        for c in self.concerns:
            # 简单词匹配：topic 中的词在消息中出现
            words = [w for w in c.topic.split() if len(w) > 1]
            if not words:
                words = [c.topic]
            hit_count = sum(1 for w in words if w.lower() in msg_lower)
            if hit_count > 0:
                score = c.intensity * (hit_count / len(words))
                max_score = max(max_score, score)

        return max_score

    @property
    def active_topics(self) -> list[str]:
        """返回当前活跃关切的主题列表。"""
        return [c.topic for c in sorted(self.concerns, key=lambda c: -c.intensity)]

    @property
    def summary(self) -> str:
        """生成关切摘要用于注入 context。"""
        active = [c for c in self.concerns if c.intensity > 0.3]
        if not active:
            return ""
        topics = [c.topic for c in sorted(active, key=lambda c: -c.intensity)[:3]]
        return f"[当前在想] {'、'.join(topics)}"

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        """简单判断两个主题是否相似。"""
        a_set = set(a.lower())
        b_set = set(b.lower())
        if not a_set or not b_set:
            return False
        overlap = len(a_set & b_set) / max(len(a_set | b_set), 1)
        return overlap > 0.5
