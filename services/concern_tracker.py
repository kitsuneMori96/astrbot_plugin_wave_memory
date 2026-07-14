"""ConcernTracker — 关切系统

维护 bot 当前在意的事情列表（动态、有时效）。
影响主动插话判断：群里聊到正在关注的事时更倾向参与。
"""

from __future__ import annotations

import asyncio
import time
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

    def __init__(
        self,
        db: WaveMemoryDB,
        bot_id: str = "",
        max_concerns: int = 10,
        *,
        scope=None,
        repository=None,
        coordinator=None,
    ):
        self.db = db
        self.bot_id = bot_id
        self.max_concerns = max_concerns
        self.scope = scope
        self.repository = repository
        self.coordinator = coordinator
        self.concerns: list[Concern] = []
        self._scoped_concerns: dict[tuple[str, str, str], list[Concern]] = {}
        # Legacy concerns 只读加载用于兼容展示，不再创建或写入。
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

    @staticmethod
    def _scope_key(scope) -> tuple[str, str, str]:
        if scope is None or getattr(scope, "session", None) is None:
            raise ValueError("scope_required")
        return scope.bot_id, scope.session.id, scope.visibility

    def _concerns_for(self, scope=None) -> list[Concern]:
        effective_scope = scope or self.scope
        if effective_scope is None:
            return self.concerns
        key = self._scope_key(effective_scope)
        bucket = self._scoped_concerns.get(key)
        if bucket is None:
            bucket = []
            self._scoped_concerns[key] = bucket
            if self.repository is not None and hasattr(self.repository, "get_state"):
                try:
                    items = self.repository.get_state(effective_scope, limit=25, offset=0)["concerns"]["items"]
                    bucket.extend(Concern(
                        topic=item["topic"],
                        intensity=float(item.get("intensity", 0.7)),
                        origin_memory_id=int(item.get("origin_memory_id") or 0),
                        bot_id=effective_scope.bot_id,
                        created_at=float(item.get("created_at") or 0),
                        last_triggered=float(item.get("last_triggered") or 0),
                    ) for item in items)
                except Exception as exc:
                    logger.debug(f"[ConcernTracker] Scoped load failed: {exc}")
        return bucket

    def _persist(self, scope=None, concerns=None, *, evidence=None):
        """全量写入指定 RuntimeScope；未注入正式依赖时仅保留对应内存分桶。"""
        effective_scope = scope or self.scope
        if self.repository is None or effective_scope is None:
            return
        active = concerns if concerns is not None else self._concerns_for(effective_scope)
        payload = [
            {
                "topic": concern.topic,
                "intensity": concern.intensity,
                "origin_memory_id": concern.origin_memory_id or None,
                "created_at": concern.created_at,
                "last_triggered": concern.last_triggered,
            }
            for concern in active
        ]
        try:
            kwargs = {"concerns": payload, "evidence": evidence}
            if self.coordinator is not None:
                self.coordinator.transaction_blocking(
                    lambda connection: self.repository.replace_concerns(
                        effective_scope, connection=connection, **kwargs
                    )
                )
            else:
                self.repository.replace_concerns(effective_scope, **kwargs)
        except Exception as e:
            logger.debug(f"[ConcernTracker] Scoped persist failed: {e}")

    def add(
        self,
        topic: str,
        origin_memory_id: int = 0,
        intensity: float = 0.7,
        *,
        scope=None,
        evidence=None,
    ):
        """在调用方 RuntimeScope 对应分桶中新增或强化关切。"""
        effective_scope = scope or self.scope
        concerns = self._concerns_for(effective_scope)
        for c in concerns:
            if self._is_similar(c.topic, topic):
                c.intensity = min(1.0, c.intensity + 0.3)
                c.last_triggered = time.time()
                self._persist(effective_scope, concerns, evidence=evidence)
                return

        if len(concerns) >= self.max_concerns:
            concerns.sort(key=lambda c: c.intensity)
            concerns.pop(0)

        concerns.append(Concern(
            topic=topic, intensity=intensity,
            origin_memory_id=origin_memory_id,
            bot_id=effective_scope.bot_id if effective_scope is not None else self.bot_id,
        ))
        self._persist(effective_scope, concerns, evidence=evidence)
        logger.debug(f"[ConcernTracker] New concern: {topic} (intensity={intensity:.2f})")

    def tick(self, *, scope=None):
        """只衰减指定 RuntimeScope 的关切。"""
        effective_scope = scope or self.scope
        concerns = self._concerns_for(effective_scope)
        now = time.time()
        for c in concerns:
            hours_elapsed = (now - c.last_triggered) / 3600
            if hours_elapsed > 0:
                c.intensity *= c.decay_rate ** hours_elapsed
                c.last_triggered = now

        before = len(concerns)
        concerns[:] = [c for c in concerns if c.intensity > 0.1]
        if len(concerns) != before:
            self._persist(effective_scope, concerns)

    def match(self, message: str, *, scope=None) -> float:
        """返回消息与指定 Scope 当前关切的最高匹配度（0-1）。"""
        concerns = self._concerns_for(scope or self.scope)
        if not concerns:
            return 0.0

        msg_lower = message.lower()
        max_score = 0.0
        for c in concerns:
            # 简单词匹配：topic 中的词在消息中出现
            words = [w for w in c.topic.split() if len(w) > 1]
            if not words:
                words = [c.topic]
            hit_count = sum(1 for w in words if w.lower() in msg_lower)
            if hit_count > 0:
                score = c.intensity * (hit_count / len(words))
                max_score = max(max_score, score)

        return max_score

    def active_topics_for(self, scope=None) -> list[str]:
        """返回指定 Scope 的活跃主题。"""
        return [
            c.topic
            for c in sorted(self._concerns_for(scope or self.scope), key=lambda c: -c.intensity)
        ]

    @property
    def active_topics(self) -> list[str]:
        return self.active_topics_for()

    def summary_for(self, scope=None) -> str:
        """生成指定 Scope 的关切摘要。"""
        active = [c for c in self._concerns_for(scope or self.scope) if c.intensity > 0.3]
        if not active:
            return ""
        topics = [c.topic for c in sorted(active, key=lambda c: -c.intensity)[:3]]
        return f"[当前在想] {'、'.join(topics)}"

    @property
    def summary(self) -> str:
        return self.summary_for()

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        """简单判断两个主题是否相似。"""
        a_set = set(a.lower())
        b_set = set(b.lower())
        if not a_set or not b_set:
            return False
        overlap = len(a_set & b_set) / max(len(a_set | b_set), 1)
        return overlap > 0.5
