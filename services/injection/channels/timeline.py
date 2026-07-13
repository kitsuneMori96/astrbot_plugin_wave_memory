"""Timeline 事件流注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

try:
    from ....domain.scope import RuntimeScope
except ImportError:  # 兼容独立测试/插件顶级加载
    from domain.scope import RuntimeScope

from ..channel_base import InjectionResult
from .safety import SafetyChannel, is_channel_allowed_in_mode


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _preview(text: str | None, limit: int = 120) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("timeline", {}))


def _timeline_cfg(ctx: Any) -> Mapping[str, Any]:
    return _mapping(_mapping(getattr(ctx, "config", {})).get("timeline", {}))


def _group_scope(ctx: Any) -> RuntimeScope | None:
    scope = getattr(ctx, "scope", None)
    if not isinstance(scope, RuntimeScope):
        return None
    if scope.visibility != "group" or scope.session is None:
        return None
    return scope


class TimelineChannel:
    """复用 memories.summary 的近期事件流通道。"""

    name = "timeline"

    def __init__(self, *, db: Any, safety_channel: SafetyChannel | None = None):
        self.db = db
        self.safety = safety_channel or SafetyChannel()

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"timeline channel disabled in {mode} mode")

        channel_cfg = _channel_cfg(ctx)
        if not _as_bool(channel_cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="timeline channel disabled by config")
        max_items = _as_int(channel_cfg.get("max_items"), 5)
        if max_items <= 0:
            return InjectionResult.empty(self.name, reason="timeline max_items is zero")
        if not getattr(ctx, "sender_id", None):
            return InjectionResult.empty(self.name, reason="timeline requires sender_id")
        if _group_scope(ctx) is None:
            return InjectionResult.empty(self.name, reason="timeline requires resolved group RuntimeScope")

        try:
            items = self._query(ctx, max_items=max_items)
            kept, filtered = self.safety.filter_items(items, ctx=ctx, text_fields=("summary",))
            if not kept:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe timeline summaries")
            lines = [f"- {item.get('day', '')}: {str(item.get('summary', ''))[:60]}" for item in kept]
            text = "[最近与此人的事件]\n" + "\n".join(lines)
            return InjectionResult.hit(
                self.name,
                text,
                items=[self._audit_item(item) for item in kept],
                filtered=[self._audit_filtered(item) for item in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    def _query(self, ctx: Any, *, max_items: int) -> list[dict[str, Any]]:
        scope = _group_scope(ctx)
        if scope is None or scope.session is None:
            return []
        cfg = _timeline_cfg(ctx)
        days = _as_int(cfg.get("days"), 7)
        now = float(getattr(ctx, "now", 0.0) or time.time())
        sender_name = str(getattr(ctx, "sender_name", "") or "")
        sender_id = str(getattr(ctx, "sender_id", "") or "")
        like_value = f"%{sender_name}%" if sender_name else f"%{sender_id}%"
        try:
            rows = self.db.conn.execute(
                """SELECT summary,
                          DATE(MAX(timestamp), 'unixepoch', 'localtime') AS day,
                          MAX(timestamp) AS ts
                     FROM memories
                    WHERE summary IS NOT NULL AND summary != '' AND summary != '日常灌水'
                      AND bot_id = ? AND session_id = ? AND visibility = ?
                      AND resolution_state = 'resolved' AND quarantine = 0
                      AND (sender_id = ? OR content LIKE ?)
                      AND timestamp > ?
                    GROUP BY summary
                    ORDER BY MAX(timestamp) DESC
                    LIMIT ?""",
                (scope.bot_id, scope.session.id, scope.visibility, sender_id, like_value, now - days * 86400, max_items),
            ).fetchall()
        except Exception:
            return []
        return [
            {"summary": row[0], "day": row[1], "timestamp": row[2], "source": "timeline"}
            for row in rows
        ]

    @staticmethod
    def _audit_item(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "summary": item.get("summary", ""),
            "day": item.get("day", ""),
            "timestamp": item.get("timestamp"),
            "preview": _preview(item.get("summary", "")),
        }

    @staticmethod
    def _audit_filtered(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = TimelineChannel._audit_item(item)
        payload["filter_reason"] = item.get("filter_reason", "filtered")
        payload["filter_channel"] = item.get("filter_channel", "timeline")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["TimelineChannel"]
