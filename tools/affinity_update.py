"""WaveMemory affinity update tool — records real relationship events."""

from __future__ import annotations

from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class WaveMemoryAffinityUpdateTool(FunctionTool[AstrAgentContext]):
    """让模型通过受约束的关系事件真实更新好感度。"""

    name: str = "wave_memory_affinity_update"
    description: str = (
        "记录一次真实的关系变化事件。只能增减某个关系维度，不能直接设置总分。"
        "必须提供 target_user、dimension、delta、event_type、reason。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "target_user": {"type": "string", "description": "目标群友名字/别名/QQ号"},
            "dimension": {
                "type": "string",
                "enum": ["familiarity", "trust", "fun", "hostility", "depth"],
                "description": "要改变的关系维度",
            },
            "delta": {"type": "number", "description": "维度变化量，受系统约束，不能直接设置总分"},
            "event_type": {
                "type": "string",
                "enum": [
                    "message_seen", "direct_reply", "bot_praised", "bot_attacked", "correction",
                    "gift_or_feed", "confession", "joke", "deep_talk", "ignored_boundary", "manual_adjustment",
                ],
                "description": "关系事件类型",
            },
            "reason": {"type": "string", "description": "为什么这件事改变了关系，必须具体"},
        },
        "required": ["target_user", "dimension", "delta", "event_type", "reason"],
    })

    db: Any = field(default=None, repr=False)
    relationship_events: Any = field(default=None, repr=False)
    bot_db_ids: dict[str, str] = field(default_factory=dict, repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        if not self.db or not self.relationship_events:
            return "关系事件系统未初始化"
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "数据库连接已断开"

        inner_ctx = getattr(ctx, "context", None)
        event = getattr(inner_ctx, "event", None)
        group_id = event.get_group_id() if event else ""
        self_id = event.get_self_id() if event else ""
        bot_id = self.bot_db_ids.get(self_id)
        if not bot_id:
            return "无法识别当前 bot，已拒绝关系更新以避免串人格"
        target = (kwargs.get("target_user") or "").strip()
        dimension = kwargs.get("dimension") or ""
        event_type = kwargs.get("event_type") or ""
        reason = (kwargs.get("reason") or "").strip()
        try:
            delta = float(kwargs.get("delta", 0))
        except Exception:
            return "delta 必须是数字"

        if not group_id:
            return "当前不是群聊上下文，无法安全更新群内关系"
        user_id = self._resolve_user(target, group_id)
        if not user_id:
            return f"没有找到目标用户「{target}」，无法更新好感度"

        try:
            result = self.relationship_events.record_event(
                bot_id=bot_id,
                group_id=group_id,
                user_id=user_id,
                event_type=event_type,
                dimension=dimension,
                delta=delta,
                reason=reason,
            )
        except Exception as e:
            return f"关系事件记录失败：{e}"

        display = self._display_name(user_id) or target or user_id
        return (
            f"已记录关系事件：{display} / {dimension} {result.applied_delta:+g}\n"
            f"当前好感度：{result.before_affection} → {result.after_affection}\n"
            f"原因：{reason}"
        )

    def _resolve_user(self, target: str, group_id: str) -> str:
        if not target:
            return ""
        row = self.db.conn.execute("SELECT qq_id FROM person_registry WHERE qq_id=?", (target,)).fetchone()
        if row:
            return row[0]
        row = self.db.conn.execute(
            "SELECT qq_id FROM person_registry WHERE display_name LIKE ? OR aliases LIKE ? ORDER BY message_count DESC LIMIT 1",
            (f"%{target}%", f"%{target}%"),
        ).fetchone()
        if row:
            return row[0]
        row = self.db.conn.execute(
            """SELECT sender_id FROM memories
               WHERE group_id=? AND sender_name LIKE ? AND sender_id IS NOT NULL AND sender_id != ''
               ORDER BY timestamp DESC LIMIT 1""",
            (group_id, f"%{target}%"),
        ).fetchone()
        return row[0] if row else ""

    def _display_name(self, user_id: str) -> str:
        row = self.db.conn.execute("SELECT display_name FROM person_registry WHERE qq_id=?", (user_id,)).fetchone()
        return row[0] if row and row[0] else user_id
