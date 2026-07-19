"""Formal reviewed BookLore search tool."""

from __future__ import annotations

from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

try:
    from ..services.identity_safety import is_identity_contamination
    from .scope_boundary import require_group_runtime_scope, scope_error_message
except ImportError:  # pragma: no cover - repository tests import tools directly
    from services.identity_safety import is_identity_contamination
    from tools.scope_boundary import require_group_runtime_scope, scope_error_message


@dataclass
class WaveMemoryBookLoreQueryTool(FunctionTool[AstrAgentContext]):
    """Search only approved reviewed projections in the active CatalogScope."""

    name: str = "wave_memory_book_lore_search"
    description: str = (
        "在当前 Bot/群 Scope 内搜索已审核的书设知识。只返回 approved reviewed projection；"
        "没有相关结果时返回空，不读取旧 raw BookLore 数据。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查找的书设、世界观或人物主题"},
            "top_k": {"type": "integer", "default": 3, "description": "返回数量，范围 1-5"},
        },
        "required": ["query"],
    })
    repository: Any = field(default=None, repr=False)
    embedding_service: Any = field(default=None, repr=False)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "请提供要搜索的书设主题"
        scope, error_code = require_group_runtime_scope(context, "book_lore.search")
        if error_code:
            return scope_error_message("书设搜索", error_code)
        if self.repository is None:
            return "正式书设投影未初始化"
        try:
            top_k = max(1, min(int(kwargs.get("top_k", 3)), 5))
        except (TypeError, ValueError):
            top_k = 3
        try:
            searcher = getattr(self.repository, "search_approved", None)
            if callable(searcher):
                rows = searcher(scope=scope, query=query, limit=top_k, min_score=0.0)
            else:
                rows = self.repository.list_approved(scope=scope, limit=top_k)
        except Exception as exc:
            return f"书设搜索失败：{exc}"
        safe_rows = []
        for row in rows or []:
            title = str(row.get("title") or "").strip()
            summary = str(row.get("summary") or row.get("content") or "").strip()
            if is_identity_contamination(f"{title} {summary}"):
                continue
            if title or summary:
                safe_rows.append((title, summary))
        if not safe_rows:
            return "当前 Scope 没有找到相关的已审核书设"
        lines = ["<world_knowledge>"]
        for title, summary in safe_rows:
            lines.append(f"{title}：{summary[:500]}" if title else summary[:500])
        lines.append("</world_knowledge>")
        return "\n".join(lines)


__all__ = ["WaveMemoryBookLoreQueryTool"]
