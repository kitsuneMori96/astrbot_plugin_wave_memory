"""WaveMemory affinity update tool — records real relationship events."""

from __future__ import annotations

from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

try:  # 兼容插件包导入和仓库测试直接导入
    from ..domain.scope import RuntimeScope
    from .scope_boundary import require_group_runtime_scope, scope_error_message
except ImportError:  # pragma: no cover - 由仓库测试直接导入 tools 使用
    from domain.scope import RuntimeScope
    from tools.scope_boundary import require_group_runtime_scope, scope_error_message


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

        runtime_scope, error_code = require_group_runtime_scope(ctx, "affinity.update")
        if error_code:
            return scope_error_message("关系更新", error_code)
        assert runtime_scope is not None

        target = (kwargs.get("target_user") or "").strip()
        dimension = kwargs.get("dimension") or ""
        event_type = kwargs.get("event_type") or ""
        reason = (kwargs.get("reason") or "").strip()
        try:
            delta = float(kwargs.get("delta", 0))
        except Exception:
            return "delta 必须是数字"

        user_id = self._resolve_user(target, runtime_scope)
        if not user_id:
            return f"没有在当前 Bot/群作用域找到目标用户「{target}」，无法更新好感度"
        target_scope = self._target_scope(runtime_scope, user_id)

        try:
            result = self.relationship_events.record_event(
                scope=target_scope,
                event_type=event_type,
                dimension=dimension,
                delta=delta,
                reason=reason,
            )
        except Exception as e:
            return f"关系事件记录失败：{e}"

        display = self._display_name(user_id, runtime_scope) or target or user_id
        return (
            f"已记录关系事件：{display} / {dimension} {result.applied_delta:+g}\n"
            f"当前好感度：{result.before_affection} → {result.after_affection}\n"
            f"原因：{reason}"
        )

    @staticmethod
    def _scope_subject_user_id(scope: RuntimeScope) -> str:
        if scope.session is None:
            return ""
        prefix = f"{scope.session.platform_id}:user:"
        principal = scope.subject_principal_id or ""
        return principal[len(prefix):] if principal.startswith(prefix) else ""

    @classmethod
    def _target_scope(cls, scope: RuntimeScope, user_id: str) -> RuntimeScope:
        if scope.visibility != "group" or scope.session is None:
            raise ValueError("relationship target requires a group RuntimeScope")
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ValueError("relationship target user_id is required")
        return RuntimeScope(
            bot_id=scope.bot_id,
            visibility="group",
            session=scope.session,
            subject_principal_id=f"{scope.session.platform_id}:user:{user_id}",
        )

    def _resolve_user(self, target: str, scope: RuntimeScope) -> str:
        """Resolve only within the active Bot + canonical group session."""
        if not target or scope.session is None:
            return ""
        current_user_id = self._scope_subject_user_id(scope)
        if target == current_user_id:
            return current_user_id
        params = (scope.session.conversation_id, scope.bot_id)
        row = self.db.conn.execute(
            """SELECT user_id FROM user_profiles
               WHERE user_id=? AND group_id=? AND bot_id=? LIMIT 1""",
            (target, *params),
        ).fetchone()
        if row:
            return str(row[0] or "")
        row = self.db.conn.execute(
            """SELECT user_id FROM user_profiles
               WHERE group_id=? AND bot_id=? AND nickname LIKE ?
               ORDER BY last_seen DESC LIMIT 1""",
            (*params, f"%{target}%"),
        ).fetchone()
        return str(row[0] or "") if row else ""

    def _display_name(self, user_id: str, scope: RuntimeScope) -> str:
        if scope.session is None:
            return user_id
        row = self.db.conn.execute(
            """SELECT nickname FROM user_profiles
               WHERE user_id=? AND group_id=? AND bot_id=?""",
            (user_id, scope.session.conversation_id, scope.bot_id),
        ).fetchone()
        return str(row[0] or user_id) if row else user_id
