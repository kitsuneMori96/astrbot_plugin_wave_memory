"""Formal BookLore search tool — 直读 raw book_lore Catalog。

书设不是 Learning reviewed projection；查询只读独立 SQLite + 向量索引。
"""

from __future__ import annotations

import sqlite3
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

try:
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext
except ImportError:  # pragma: no cover - repository tests without AstrBot
    from typing import Any, Generic, TypeVar

    _T = TypeVar("_T")

    class FunctionTool(Generic[_T]):  # type: ignore[no-redef]
        pass

    class ContextWrapper(Generic[_T]):  # type: ignore[no-redef]
        pass

    AstrAgentContext = Any  # type: ignore[misc,assignment]

try:
    from ..domain.scope import CatalogScope
    from ..services.identity_safety import is_identity_contamination
    from .scope_boundary import require_catalog_scope, scope_error_message
except ImportError:  # pragma: no cover - repository tests import tools directly
    from domain.scope import CatalogScope
    from services.identity_safety import is_identity_contamination
    from tools.scope_boundary import require_catalog_scope, scope_error_message


@dataclass
class WaveMemoryBookLoreQueryTool(FunctionTool[AstrAgentContext]):
    """Search the external BookLore catalog by semantic similarity."""

    name: str = "wave_memory_book_lore_search"
    description: str = (
        "在书设知识库中语义搜索世界观、人物、势力与创作设定。"
        "直接查询正式 BookLore Catalog，不依赖学习晋升或 reviewed projection。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要查找的书设、世界观或人物主题"},
            "top_k": {"type": "integer", "default": 3, "description": "返回数量，范围 1-5"},
            "type_filter": {
                "type": "string",
                "default": "",
                "description": "可选实体类型过滤（如 person/force/location/item）",
            },
        },
        "required": ["query"],
    })
    book_lore_index: Any = field(default=None, repr=False)
    embedding_service: Any = field(default=None, repr=False)
    lore_db_path: str = field(default="", repr=False)
    catalog_scope: CatalogScope | None = field(default=None, repr=False)
    # 兼容旧装配字段；正式路径不再读取 projection repository。
    repository: Any = field(default=None, repr=False)

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        _, error_code = require_catalog_scope(self.catalog_scope, "catalog.read")
        if error_code:
            return scope_error_message("书设搜索", error_code)

        query = str(kwargs.get("query") or "").strip()
        if not query:
            return "请提供要搜索的书设主题"
        if self.book_lore_index is None:
            return "书设索引未初始化"
        if self.embedding_service is None:
            return "Embedding 服务未初始化"
        if not self.lore_db_path:
            return "书设数据库路径未配置"

        try:
            top_k = max(1, min(int(kwargs.get("top_k", 3)), 5))
        except (TypeError, ValueError):
            top_k = 3
        type_filter = str(kwargs.get("type_filter") or "").strip()

        try:
            query_vec = await self.embedding_service.get_embedding(query)
            if query_vec is None:
                return "Embedding 生成失败"

            entity_results = self.book_lore_index.search_entities(query_vec, k=top_k) or []
            notes_results = []
            search_notes = getattr(self.book_lore_index, "search_notes", None)
            if callable(search_notes):
                notes_results = search_notes(query_vec, k=top_k) or []
            community_results = []
            search_communities = getattr(self.book_lore_index, "search_communities", None)
            if callable(search_communities):
                community_results = search_communities(query_vec, k=top_k) or []

            conn = sqlite3.connect(self.lore_db_path)
            try:
                safe_rows: list[tuple[str, str]] = []

                for entity_id, score in entity_results:
                    if float(score or 0.0) < 0.3:
                        continue
                    row = conn.execute(
                        "SELECT title, type, description FROM book_entities WHERE id = ?",
                        (entity_id,),
                    ).fetchone()
                    if not row:
                        continue
                    title, etype, desc = row
                    if type_filter and str(etype or "") != type_filter:
                        continue
                    summary = (desc or "")[:500]
                    if is_identity_contamination(f"{title} {summary}"):
                        continue
                    label = f"{title}({etype})" if etype else str(title or "")
                    if label or summary:
                        safe_rows.append((label, summary))

                for note_id, score in notes_results:
                    if float(score or 0.0) < 0.3:
                        continue
                    row = conn.execute(
                        "SELECT title, content FROM book_notes WHERE id = ?",
                        (note_id,),
                    ).fetchone()
                    if not row:
                        continue
                    title, content = row
                    summary = (content or "")[:500]
                    if is_identity_contamination(f"{title} {summary}"):
                        continue
                    if title or summary:
                        safe_rows.append((str(title or ""), summary))

                for community_id, score in community_results:
                    if float(score or 0.0) < 0.3:
                        continue
                    row = conn.execute(
                        "SELECT title, summary FROM book_communities WHERE id = ?",
                        (community_id,),
                    ).fetchone()
                    if not row:
                        continue
                    title, summary = row
                    summary = (summary or "")[:500]
                    if is_identity_contamination(f"{title} {summary}"):
                        continue
                    if title or summary:
                        safe_rows.append((str(title or ""), summary))
            finally:
                conn.close()

            # 去重并截断到 top_k
            deduped: list[tuple[str, str]] = []
            seen: set[str] = set()
            for title, summary in safe_rows:
                key = f"{title}|{summary[:80]}"
                if key in seen:
                    continue
                seen.add(key)
                deduped.append((title, summary))
                if len(deduped) >= top_k:
                    break

            if not deduped:
                return f"没有找到与「{query}」相关的书设信息"

            lines = ["<world_knowledge>"]
            for title, summary in deduped:
                lines.append(f"{title}：{summary}" if title else summary)
            lines.append("</world_knowledge>")
            return "\n".join(lines)
        except Exception as exc:
            return f"书设搜索失败：{exc}"


__all__ = ["WaveMemoryBookLoreQueryTool"]
