"""MoodTrajectory — 情绪轨迹

从单点情绪变成轨迹：记录每次高强度交互的情绪快照，
生成"最近情绪走势"摘要注入 context。
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Optional

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - repository tests run without AstrBot
    import logging
    logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    try:
        from ..engine.database import WaveMemoryDB
    except ImportError:  # pragma: no cover
        from engine.database import WaveMemoryDB


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

    def __init__(
        self,
        db: WaveMemoryDB,
        bot_id: str = "",
        window_size: int = 20,
        *,
        scope=None,
        repository=None,
        coordinator=None,
    ):
        self.db = db
        self.bot_id = bot_id
        self.scope = scope
        self.repository = repository
        self.coordinator = coordinator
        self.snapshots: deque[MoodSnapshot] = deque(maxlen=window_size)
        self._scoped_snapshots: dict[tuple[str, str, str], deque[MoodSnapshot]] = {}
        # Legacy mood_snapshots 只作为审计来源读取；正式持久化必须走 scoped repository。
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

    @staticmethod
    def _scope_key(scope) -> tuple[str, str, str]:
        if scope is None or getattr(scope, "session", None) is None:
            raise ValueError("scope_required")
        return scope.bot_id, scope.session.id, scope.visibility

    def _snapshots_for(self, scope=None) -> deque[MoodSnapshot]:
        effective_scope = scope or self.scope
        if effective_scope is None:
            return self.snapshots
        key = self._scope_key(effective_scope)
        bucket = self._scoped_snapshots.get(key)
        if bucket is None:
            bucket = deque(maxlen=self.snapshots.maxlen)
            self._scoped_snapshots[key] = bucket
            if self.repository is not None and hasattr(self.repository, "get_state"):
                try:
                    mood = self.repository.get_state(effective_scope, limit=25, offset=0).get("mood", {})
                    if mood.get("state") == "known":
                        components = mood.get("components") or {}
                        bucket.append(MoodSnapshot(
                            timestamp=float(mood.get("observed_at") or time.time()),
                            valence=float(components.get("valence", mood.get("value", 0.0))),
                            arousal=float(components.get("arousal", 0.0)),
                            cause=str(mood.get("cause") or ""),
                            bot_id=effective_scope.bot_id,
                        ))
                except Exception as exc:
                    logger.debug(f"[MoodTrajectory] Scoped load failed: {exc}")
        return bucket

    def record(
        self,
        valence: float,
        arousal: float,
        cause: str = "",
        *,
        scope=None,
        evidence=None,
    ):
        """按调用方传入的 RuntimeScope 记录，禁止跨会话共享正式状态。"""
        effective_scope = scope or self.scope
        now = time.time()
        snap = MoodSnapshot(
            timestamp=now,
            valence=valence,
            arousal=arousal,
            cause=cause,
            bot_id=effective_scope.bot_id if effective_scope is not None else self.bot_id,
        )
        self._snapshots_for(effective_scope).append(snap)

        if self.repository is None or effective_scope is None:
            return
        try:
            kwargs = {
                "valence": valence,
                "arousal": arousal,
                "cause": cause,
                "evidence": evidence,
                "observed_at": now,
            }
            if self.coordinator is not None:
                self.coordinator.transaction_blocking(
                    lambda connection: self.repository.upsert_mood(
                        effective_scope, connection=connection, **kwargs
                    )
                )
            else:
                self.repository.upsert_mood(effective_scope, **kwargs)
        except Exception as e:
            logger.debug(f"[MoodTrajectory] Scoped persist failed: {e}")

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
