"""Briefing 群聊简报注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

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


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("briefing", {}))


class BriefingChannel:
    """群聊简报：bot 上次回复以来的消息流水。"""

    name = "briefing"

    def __init__(self, *, db: Any, safety_channel: SafetyChannel | None = None):
        self.db = db
        self.safety = safety_channel or SafetyChannel()

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()

        # 私聊跳过
        group_id = getattr(ctx, "group_id", None) or ""
        if group_id.startswith("private:"):
            return InjectionResult.empty(self.name, reason="private chat skipped")

        # mode 门控
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"briefing disabled in {mode} mode")

        # 配置门控
        channel_cfg = _channel_cfg(ctx)
        if not _as_bool(channel_cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="briefing disabled by config")

        max_items = _as_int(channel_cfg.get("max_items"), 30)
        if max_items <= 0:
            return InjectionResult.empty(self.name, reason="briefing max_items is zero")

        if not group_id:
            return InjectionResult.empty(self.name, reason="briefing requires group_id")

        try:
            bot_id = getattr(ctx, "bot_profile_id", "") or ""
            now = float(getattr(ctx, "now", 0.0) or time.time())

            # 1. 查 bot 最后回复时间（兼容两种 sender_id 写入方式）
            last_bot_ts = self._get_last_bot_ts(group_id, bot_id)

            # 2. 查询消息
            if last_bot_ts is None:
                messages = self._query_recent(group_id, max_items=20)
            else:
                messages = self._query_since(group_id, bot_id, last_bot_ts, max_items)

            # 3. safety 过滤
            kept, filtered = self.safety.filter_items(
                messages, ctx=ctx, text_fields=("content",)
            )

            # 4. 格式化 + token 预算累计检查
            lines: list[str] = []
            total_tokens = 0
            for msg in kept:
                name = (msg.get("sender_name") or "用户").strip()
                ts = msg.get("timestamp", 0)
                time_str = time.strftime("%H:%M", time.localtime(ts))
                content = (msg.get("content") or "")[:100]
                line = f"{name}({time_str}): {content}"
                line_tokens = len(line) // 2
                if total_tokens + line_tokens > 1000:
                    break
                lines.append(line)
                total_tokens += line_tokens

            if not lines and not getattr(ctx, "message", ""):
                return InjectionResult.empty(
                    self.name, latency_ms=self._latency_ms(started), reason="no messages"
                )

            # 5. 组装：当前 @ 消息 + 历史
            parts = ["[简报]"]
            ctx_msg = getattr(ctx, "message", "")
            if ctx_msg:
                sender_name = getattr(ctx, "sender_name", "") or "用户"
                parts.append(f"⚡ {sender_name}: {ctx_msg[:100]}")
            if lines:
                parts.append("[之前的消息]")
                parts.extend(lines)

            text = "\n".join(parts)
            return InjectionResult.hit(
                self.name,
                text,
                items=[self._audit_item(item) for item in kept],
                filtered=[self._audit_filtered(item) for item in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    def _get_last_bot_ts(self, group_id: str, bot_id: str) -> float | None:
        """查 bot 最后回复时间（兼容 sender_id='bot' 和真实 QQ 号）。"""
        row = self.db.conn.execute(
            """SELECT MAX(timestamp) FROM memories
               WHERE group_id = ? AND sender_id IN ('bot', ?)""",
            (group_id, bot_id),
        ).fetchone()
        return row[0] if row else None

    def _query_recent(self, group_id: str, max_items: int = 20) -> list[dict[str, Any]]:
        """重生后无 bot 记录，取最近 N 条。"""
        rows = self.db.conn.execute(
            """SELECT sender_name, content, timestamp FROM memories
               WHERE group_id = ? AND sender_id IS NOT NULL
               ORDER BY timestamp DESC LIMIT ?""",
            (group_id, max_items),
        ).fetchall()
        rows.reverse()
        return [
            {"sender_name": r[0], "content": r[1], "timestamp": r[2], "source": "briefing"}
            for r in rows
        ]

    def _query_since(
        self, group_id: str, bot_id: str, since_ts: float, max_items: int = 30
    ) -> list[dict[str, Any]]:
        """查 bot 回复之后的消息（含 bot 自己的最后回复作为上下文起点）。"""
        rows = self.db.conn.execute(
            """SELECT sender_name, content, timestamp FROM memories
               WHERE group_id = ? AND timestamp > ?
               ORDER BY timestamp DESC LIMIT ?""",
            (group_id, since_ts, max_items),
        ).fetchall()
        rows.reverse()
        return [
            {"sender_name": r[0], "content": r[1], "timestamp": r[2], "source": "briefing"}
            for r in rows
        ]

    @staticmethod
    def _audit_item(item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sender_name": item.get("sender_name", ""),
            "timestamp": item.get("timestamp"),
            "preview": str(item.get("content", ""))[:80],
        }

    @staticmethod
    def _audit_filtered(item: Mapping[str, Any]) -> dict[str, Any]:
        payload = BriefingChannel._audit_item(item)
        payload["filter_reason"] = item.get("filter_reason", "filtered")
        payload["filter_channel"] = item.get("filter_channel", "briefing")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["BriefingChannel"]
