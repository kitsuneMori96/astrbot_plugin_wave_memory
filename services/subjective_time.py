"""SubjectiveTime — 主观时间感

用重要事件锚定时间，让 bot 说"上次你来找我之后就没出现过"
而不是"2026-05-15 你发过消息"。
"""

from __future__ import annotations

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


class TimeAnchor:
    """一个时间锚点（重要事件）。"""
    __slots__ = ("event_summary", "timestamp", "emotional_weight", "bot_id")

    def __init__(self, event_summary: str, timestamp: float, emotional_weight: float = 0.5, bot_id: str = ""):
        self.event_summary = event_summary
        self.timestamp = timestamp
        self.emotional_weight = emotional_weight
        self.bot_id = bot_id


class SubjectiveTime:
    """主观时间引擎 — 用锚点描述时间间隔。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        bot_id: str = "",
        max_anchors: int = 20,
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
        self.anchors: list[TimeAnchor] = []
        self._scoped_anchors: dict[tuple[str, str, str], list[TimeAnchor]] = {}
        self.max_anchors = max_anchors
        # Legacy time_anchors 仅作为审计来源读取。
        self._load()

    def _ensure_table(self):
        try:
            self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS time_anchors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_summary TEXT NOT NULL,
                    timestamp REAL,
                    emotional_weight REAL DEFAULT 0.5,
                    bot_id TEXT DEFAULT ''
                )
            """)
            self.db.conn.commit()
        except Exception:
            pass

    def _load(self):
        try:
            rows = self.db.conn.execute(
                "SELECT event_summary, timestamp, emotional_weight FROM time_anchors "
                "WHERE bot_id = ? ORDER BY timestamp DESC LIMIT ?",
                (self.bot_id, self.max_anchors),
            ).fetchall()
            self.anchors = [
                TimeAnchor(event_summary=r[0], timestamp=r[1], emotional_weight=r[2], bot_id=self.bot_id)
                for r in rows
            ]
        except Exception:
            pass

    @staticmethod
    def _scope_key(scope) -> tuple[str, str, str]:
        if scope is None or getattr(scope, "session", None) is None:
            raise ValueError("scope_required")
        return scope.bot_id, scope.session.id, scope.visibility

    def _anchors_for(self, scope=None) -> list[TimeAnchor]:
        effective_scope = scope or self.scope
        if effective_scope is None:
            return self.anchors
        key = self._scope_key(effective_scope)
        bucket = self._scoped_anchors.get(key)
        if bucket is None:
            bucket = []
            self._scoped_anchors[key] = bucket
            if self.repository is not None and hasattr(self.repository, "get_state"):
                try:
                    items = self.repository.get_state(effective_scope, limit=self.max_anchors, offset=0)["timeline"]["items"]
                    bucket.extend(TimeAnchor(
                        event_summary=item["event_summary"],
                        timestamp=float(item.get("timestamp") or 0),
                        emotional_weight=float(item.get("emotional_weight") or 0.5),
                        bot_id=effective_scope.bot_id,
                    ) for item in items)
                except Exception as exc:
                    logger.debug(f"[SubjectiveTime] Scoped load failed: {exc}")
        return bucket

    def add_anchor(
        self,
        event_summary: str,
        emotional_weight: float = 0.5,
        timestamp: float = None,
        *,
        scope=None,
        evidence=None,
    ):
        """在调用方 RuntimeScope 内添加时间锚点。"""
        effective_scope = scope or self.scope
        ts = timestamp or time.time()
        anchor = TimeAnchor(event_summary=event_summary, timestamp=ts, emotional_weight=emotional_weight, bot_id=getattr(effective_scope, "bot_id", self.bot_id))
        anchors = self._anchors_for(effective_scope)
        anchors.insert(0, anchor)
        if len(anchors) > self.max_anchors:
            del anchors[self.max_anchors:]

        if self.repository is None or effective_scope is None:
            return
        try:
            kwargs = {
                "event_summary": event_summary,
                "emotional_weight": emotional_weight,
                "timestamp": ts,
                "evidence": evidence,
            }
            if self.coordinator is not None:
                self.coordinator.transaction_blocking(
                    lambda connection: self.repository.add_timeline_event(
                        effective_scope, connection=connection, **kwargs
                    )
                )
            else:
                self.repository.add_timeline_event(effective_scope, **kwargs)
        except Exception as e:
            logger.debug(f"[SubjectiveTime] Scoped persist failed: {e}")

    def describe_interval(self, target_timestamp: float, *, scope=None) -> str:
        """将绝对时间差转为指定 RuntimeScope 内的主观描述。"""
        elapsed = time.time() - target_timestamp

        # 查找同一作用域内最近的锚点
        nearest_anchor = self._find_nearest_anchor(target_timestamp, scope=scope)

        if nearest_anchor and elapsed < 7 * 86400:
            return f"{nearest_anchor.event_summary}之后"

        if elapsed < 300:
            return "刚才"
        elif elapsed < 3600:
            mins = int(elapsed / 60)
            return f"{mins}分钟前"
        elif elapsed < 86400:
            hours = int(elapsed / 3600)
            return f"今天{'早些时候' if hours > 3 else f'{hours}小时前'}"
        elif elapsed < 2 * 86400:
            return "昨天"
        elif elapsed < 3 * 86400:
            return "前天"
        elif elapsed < 7 * 86400:
            return "这周"
        elif elapsed < 14 * 86400:
            return "上周"
        elif elapsed < 30 * 86400:
            return "这个月"
        else:
            months = int(elapsed / (30 * 86400))
            if months <= 1:
                return "上个月"
            return f"{months}个月前" if months < 6 else "很久以前"

    def describe_absence(self, user_id: str, last_seen_ts: float) -> Optional[str]:
        """描述某个用户的缺席时间。如果太短（<1h）返回 None。"""
        elapsed = time.time() - last_seen_ts
        if elapsed < 3600:
            return None
        return f"上次见到是{self.describe_interval(last_seen_ts)}"

    def get_period_context(self) -> str:
        """获取当前时间段的上下文感知。"""
        hour = int(time.strftime("%H"))
        weekday = int(time.strftime("%w"))  # 0=Sun

        period = ""
        if 0 <= hour < 6:
            period = "深夜"
        elif 6 <= hour < 9:
            period = "早上"
        elif 9 <= hour < 12:
            period = "上午"
        elif 12 <= hour < 14:
            period = "中午"
        elif 14 <= hour < 18:
            period = "下午"
        elif 18 <= hour < 22:
            period = "晚上"
        else:
            period = "深夜"

        day_name = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][weekday]
        return f"{day_name}{period}"

    def _find_nearest_anchor(self, target_ts: float, *, scope=None) -> Optional[TimeAnchor]:
        """在指定 RuntimeScope 内找到距目标时间最近且在其之前的锚点。"""
        best = None
        best_diff = float('inf')
        for anchor in self._anchors_for(scope):
            diff = target_ts - anchor.timestamp
            if 0 < diff < best_diff:
                best_diff = diff
                best = anchor
        return best if best and best_diff < 7 * 86400 else None  # 7天内的锚点才有意义
