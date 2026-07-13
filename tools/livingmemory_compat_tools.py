"""LivingMemory-style Agent tool aliases backed by WaveMemory facade."""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

try:
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext
except Exception:  # pragma: no cover - 本地单测未安装 AstrBot SDK 时的轻量兜底
    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class FunctionTool(Generic[_T]):
        pass

    class ContextWrapper(Generic[_T]):
        pass

    class AstrAgentContext:
        pass

try:
    from services.agent.permission_policy import check_agent_action
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ..services.agent.permission_policy import check_agent_action

try:  # 兼容插件包导入和仓库测试直接导入
    from ..domain.scope import RuntimeScope
    from .scope_boundary import extract_event_runtime_scope, require_group_runtime_scope, scope_error_message
except ImportError:  # pragma: no cover - 由仓库测试直接导入 tools 使用
    from domain.scope import RuntimeScope
    from tools.scope_boundary import extract_event_runtime_scope, require_group_runtime_scope, scope_error_message


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _extract_runtime_scope(ctx: ContextWrapper[AstrAgentContext] | None) -> RuntimeScope | None:
    """兼容旧私有导入；不从 legacy session/group 字段重新推断 Scope。"""
    return extract_event_runtime_scope(ctx)


def _parse_metadata(value: Any) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, dict) else {}
        except Exception:
            return {"raw_metadata": value}
    return {"raw_metadata": str(value)}


@dataclass
class RecallLongTermMemoryTool(FunctionTool[AstrAgentContext]):
    """LivingMemory-compatible recall alias backed by WaveMemory search."""

    name: str = "recall_long_term_memory"
    description: str = "兼容 LivingMemory 的长期记忆搜索别名。内部调用 WaveMemory search_memories，只读检索。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要搜索的长期记忆关键词或问题"},
            "k": {"type": "integer", "description": "返回数量，默认 5", "default": 5},
            "session_id": {"type": "string", "description": "可选会话/群聊范围"},
            "persona_id": {"type": "string", "description": "可选人格/角色标识"},
        },
        "required": ["query"],
    })
    memory_engine: Any = field(default=None, repr=False)
    permission_action: str = "search_memory"

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        if not self.memory_engine:
            return "LivingMemory 兼容记忆 facade 未初始化"
        query = str(kwargs.get("query", "")).strip()
        if not query:
            return "请提供要搜索的记忆 query"
        try:
            k = int(kwargs.get("k", 5) or 5)
        except (TypeError, ValueError):
            k = 5
        runtime_scope, error_code = require_group_runtime_scope(ctx, "memory.message.read")
        if error_code:
            return _json_payload({"status": "error", "message": scope_error_message("兼容记忆搜索", error_code)})
        assert runtime_scope is not None
        session_id = kwargs.get("session_id") or None
        if session_id and str(session_id) not in {
            runtime_scope.session.conversation_id,
            runtime_scope.session.id,
        }:
            return _json_payload({"status": "error", "message": "session_id 与当前作用域不一致，已拒绝搜索"})
        persona_id = kwargs.get("persona_id") or None
        if persona_id and str(persona_id) != runtime_scope.bot_id:
            return _json_payload({"status": "error", "message": "persona_id 与当前 Bot 作用域不一致，已拒绝搜索"})
        results = await self.memory_engine.search_memories(
            query,
            k=k,
            session_id=session_id,
            persona_id=persona_id,
            scope=runtime_scope,
        )
        if getattr(self.memory_engine, "last_error", None):
            return _json_payload({"status": "error", "message": self.memory_engine.last_error})
        return _json_payload({"status": "ok", "results": results})


@dataclass
class MemorizeLongTermMemoryTool(FunctionTool[AstrAgentContext]):
    """LivingMemory-compatible memorize alias backed by WaveMemory writer."""

    name: str = "memorize_long_term_memory"
    description: str = "兼容 LivingMemory 的长期记忆写入别名。内部调用 WaveMemory add_memory，进入同一写入队列。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容"},
            "session_id": {"type": "string", "description": "可选会话/群聊范围"},
            "persona_id": {"type": "string", "description": "可选人格/角色标识"},
            "importance": {"type": "number", "description": "重要性，默认 0.7", "default": 0.7},
            "metadata": {"type": "object", "description": "可选来源元数据"},
        },
        "required": ["content"],
    })
    memory_engine: Any = field(default=None, repr=False)
    permission_action: str = "remember_memory"

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        if not self.memory_engine:
            return "LivingMemory 兼容记忆 facade 未初始化"
        content = str(kwargs.get("content", "")).strip()
        if not content:
            return "请提供要记住的 content"
        try:
            importance = float(kwargs.get("importance", 0.7) or 0.7)
        except (TypeError, ValueError):
            importance = 0.7
        runtime_scope, error_code = require_group_runtime_scope(ctx, "memory.message.write")
        if error_code:
            return _json_payload({"status": "error", "message": scope_error_message("兼容记忆写入", error_code)})
        assert runtime_scope is not None
        session_id = kwargs.get("session_id") or None
        if session_id and str(session_id) not in {
            runtime_scope.session.conversation_id,
            runtime_scope.session.id,
        }:
            return _json_payload({"status": "error", "message": "session_id 与当前作用域不一致，已拒绝写入"})
        persona_id = kwargs.get("persona_id") or None
        if persona_id and str(persona_id) != runtime_scope.bot_id:
            return _json_payload({"status": "error", "message": "persona_id 与当前 Bot 作用域不一致，已拒绝写入"})
        metadata = _parse_metadata(kwargs.get("metadata"))
        event = getattr(getattr(ctx, "context", None), "event", None)
        event_id = getattr(event, "message_id", None) if event else None
        if event_id not in {None, ""}:
            metadata.setdefault("event_id", str(event_id))
        metadata.setdefault("origin_kind", "livingmemory_compat_tool")
        memory_id = await self.memory_engine.add_memory(
            content,
            session_id=session_id,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
            scope=runtime_scope,
        )
        if not memory_id:
            return _json_payload({"status": "error", "message": getattr(self.memory_engine, "last_error", "写入失败") or "写入失败"})
        return _json_payload({"status": "queued", "memory_id": memory_id})


def build_livingmemory_compat_tools(memory_engine: Any, *, enabled: bool = False) -> list[Any]:
    """Return LivingMemory-style alias tools only when explicitly enabled."""
    if not enabled or not memory_engine:
        return []
    return [
        RecallLongTermMemoryTool(memory_engine=memory_engine),
        MemorizeLongTermMemoryTool(memory_engine=memory_engine),
    ]


__all__ = [
    "RecallLongTermMemoryTool",
    "MemorizeLongTermMemoryTool",
    "build_livingmemory_compat_tools",
]
