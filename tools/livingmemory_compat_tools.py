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


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _extract_session_id(ctx: ContextWrapper[AstrAgentContext] | None, explicit: Any = None) -> str | None:
    if explicit:
        return str(explicit)
    try:
        event = getattr(getattr(ctx, "context", None), "event", None)
        if event:
            group_id = event.get_group_id()
            return str(group_id) if group_id else None
    except Exception:
        return None
    return None


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
        session_id = _extract_session_id(ctx, kwargs.get("session_id"))
        persona_id = kwargs.get("persona_id") or None
        results = await self.memory_engine.search_memories(query, k=k, session_id=session_id, persona_id=persona_id)
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
        session_id = _extract_session_id(ctx, kwargs.get("session_id"))
        persona_id = kwargs.get("persona_id") or None
        metadata = _parse_metadata(kwargs.get("metadata"))
        memory_id = await self.memory_engine.add_memory(
            content,
            session_id=session_id,
            persona_id=persona_id,
            importance=importance,
            metadata=metadata,
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
