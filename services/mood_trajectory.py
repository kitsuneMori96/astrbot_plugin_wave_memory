"""MoodTrajectory — 情绪轨迹

从单点情绪变成轨迹：记录每次高强度交互的情绪快照，
生成"最近情绪走势"摘要注入 context。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional

from astrbot.api import logger

from ..engine.database import WaveMemoryDB


class MoodSnapshot:
    """一次情绪快照。"""
    __slots__ = ("timestamp", "valence", "arousal", "cause", "bot_id")

    def __init__(self, timestamp: float, valence: float, arousal: float, cause: str, bot_id: str = ""):
        self.timestamp = timestamp
        self.valence = valence      # -1(极差) ~ +1(极好)
        self.arousal = arousal      # 0(平静) ~ 1(激动)
        self.cause = cause
        self.bot_id = bot_id


class MoodTrajectory:
    """情绪轨迹 — 维护最近 N 个情绪快照。"""

    def __init__(self, db: WaveMemoryDB, bot_id: str = "", window_size: int = 20):
        self.db = db
        self.bot_id = bot_id
        self.snapshots: deque[MoodSnapshot] = deque(maxlen=window_size)
        self._ensure_table()
        self._load()

    def _ensure_table(self):
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS mood_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT DEFAULT '',
                    timestamp REAL,
                    valence REAL,
                    arousal REAL,
                    cause TEXT
                )
            """)
            self.db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mood_bot_ts ON mood_snapshots(bot_id, timestamp)"
            )
            self.db.conn.commit()
        except Exception:
            pass

    def _load(self):
        """启动时加载最近的快照。"""
        try:
            rows = self.db.conn.execute(
                "SELECT timestamp, valence, arousal, cause FROM mood_snapshots "
                "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT ?",
                (self.bot_id, self.snapshots.maxlen),
            ).fetchall()
            for r in reversed(rows):  # 从旧到新
                self.snapshots.append(MoodSnapshot(
                    timestamp=r[0], valence=r[1], arousal=r[2], cause=r[3], bot_id=self.bot_id,
                ))
        except Exception:
            pass

    def record(self, valence: float, arousal: float, cause: str = ""):
        """记录一次情绪快照。

        Args:
            valence: -1(极差) ~ +1(极好)
            arousal: 0(平静) ~ 1(激动)
            cause: 触发原因（简短描述）
        """
        now = time.time()
        snap = MoodSnapshot(timestamp=now, valence=valence, arousal=arousal, cause=cause, bot_id=self.bot_id)
        self.snapshots.append(snap)

        # 持久化
        try:
            self.db.conn.execute(
                "INSERT INTO mood_snapshots (bot_id, timestamp, valence, arousal, cause) VALUES (?, ?, ?, ?, ?)",
                (self.bot_id, now, valence, arousal, cause),
            )
            self.db.conn.commit()
        except Exception as e:
            logger.debug(f"[MoodTrajectory] Persist failed: {e}")

    @property
    def current_valence(self) -> float:
        """当前情绪基调（最近 5 个快照的加权平均）。"""
        if not self.snapshots:
            return 0.0
        recent = list(self.snapshots)[-5:]
        # 越近权重越高
        weights = [0.1, 0.15, 0.2, 0.25, 0.3][-len(recent):]
        total_w = sum(weights)
        return sum(s.valence * w for s, w in zip(recent, weights)) / total_w

    @property
    def summary(self) -> str:
        """生成最近情绪摘要，用于注入 context。"""
        if not self.snapshots:
            return ""

        avg_valence = self.current_valence
        recent_3 = list(self.snapshots)[-3:]

        if avg_valence > 0.3:
            mood_word = "心情不错"
        elif avg_valence > 0.1:
            mood_word = "还行"
        elif avg_valence > -0.1:
            mood_word = "平平淡淡"
        elif avg_valence > -0.3:
            mood_word = "有点烦"
        else:
            mood_word = "心情不太好"

        # 取最近有原因的快照
        causes = [s.cause for s in recent_3 if s.cause]
        cause_text = f"（{'、'.join(causes[-2:])}）" if causes else ""

        return f"[近期状态] {mood_word}{cause_text}"

    @property
    def is_upset(self) -> bool:
        """是否处于低落状态（影响对所有人的态度）。"""
        return self.current_valence < -0.2

    @property
    def is_happy(self) -> bool:
        """是否处于愉快状态。"""
        return self.current_valence > 0.3
