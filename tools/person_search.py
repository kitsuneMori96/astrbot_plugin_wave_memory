"""Wave Memory 人物检索工具。

旧 person_registry / memory_mentions read-model 不携带完整 canonical scope，
在 scoped social read-model 落地前不能暴露给 LLM。
"""

from __future__ import annotations

from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

try:
    from .scope_boundary import scope_error_message
except ImportError:  # pragma: no cover - direct tools imports in isolated tests
    from tools.scope_boundary import scope_error_message


@dataclass
class WaveMemoryPersonSearchTool(FunctionTool[AstrAgentContext]):
    """已隔离：全局人物索引无法证明当前 RuntimeScope。"""

    name: str = "wave_memory_person_search"
    description: str = "人物检索正在进行 Scope 数据面迁移，当前不可用。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "person": {"type": "string", "description": "迁移完成后可查询的人物"},
            "query_type": {
                "type": "string",
                "enum": ["recent", "about", "social", "profile"],
                "default": "recent",
            },
            "limit": {"type": "integer", "default": 8},
        },
        "required": ["person"],
    })

    db: Any = field(default=None, repr=False)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        # 不可用裸 group_id 或全局 qq_id 为 legacy 行反推 platform+bot+session。
        return scope_error_message("人物检索", "scope_migration_required")


__all__ = ["WaveMemoryPersonSearchTool"]
