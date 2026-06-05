"""Wave Memory 书设知识搜索工具 — 语义搜索 + 实体图谱

从 hermes wavememory-mcp 的 mcp_tools.py (tool_book_lore_graph) 和
engine/book_lore_index.py 适配为 AstrBot FunctionTool 格式。
"""

from __future__ import annotations

import sqlite3
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class BookLoreSearchTool(FunctionTool[AstrAgentContext]):
    """书设语义搜索工具：在书设知识库中搜索实体、笔记和关系。"""

    name: str = "book_lore_search"
    description: str = (
        "在书设知识库中语义搜索。可以搜索人物、势力、地点等实体信息，"
        "也可以搜索创作笔记和世界观设定。支持按类型过滤。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索内容，自然语言描述你想找的书设信息"
            },
            "type_filter": {
                "type": "string",
                "description": "过滤实体类型（如 person/force/location/item），留空搜全部",
                "default": ""
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 5",
                "default": 5
            },
        },
        "required": ["query"],
    })

    # 运行时注入
    book_lore_index: Any = field(default=None, repr=False)
    embedding_service: Any = field(default=None, repr=False)
    db: Any = field(default=None, repr=False)
    lore_db_path: str = field(default="", repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        query = kwargs.get("query", "").strip()
        type_filter = kwargs.get("type_filter", "")
        limit = int(kwargs.get("limit", 5))

        if not query:
            return "请提供搜索内容"

        if not self.book_lore_index:
            return "书设索引未初始化"

        if not self.embedding_service:
            return "Embedding 服务未初始化"

        try:
            # 获取查询向量
            query_vec = await self.embedding_service.get_embedding(query)
            if query_vec is None:
                return "Embedding 生成失败"

            results = []

            # 搜索实体
            entity_results = self.book_lore_index.search_entities(query_vec, k=limit)
            # 搜索笔记
            notes_results = self.book_lore_index.search_notes(query_vec, k=limit)

            # 从 lore db 获取实体详情
            conn_lore = self._get_lore_conn()
            if not conn_lore:
                return "书设数据库连接失败"

            try:
                # 实体结果
                for entity_id, score in entity_results:
                    if score < 0.3:
                        continue
                    row = conn_lore.execute(
                        "SELECT title, type, description FROM book_entities WHERE id = ?",
                        (entity_id,)
                    ).fetchone()
                    if row:
                        title, etype, desc = row
                        if type_filter and etype != type_filter:
                            continue
                        desc_short = (desc or "")[:150]
                        results.append(f"[实体] {title}({etype}) 相似度:{score:.2f}\n  {desc_short}")

                # 笔记结果
                for note_id, score in notes_results:
                    if score < 0.3:
                        continue
                    row = conn_lore.execute(
                        "SELECT title, content FROM book_notes WHERE id = ?",
                        (note_id,)
                    ).fetchone()
                    if row:
                        title, content = row
                        content_short = (content or "")[:200]
                        results.append(f"[笔记] {title} 相似度:{score:.2f}\n  {content_short}")

            finally:
                conn_lore.close()

            if not results:
                return f"没有找到与「{query}」相关的书设信息"

            return f"找到 {len(results)} 条书设信息:\n\n" + "\n\n".join(results[:limit])

        except Exception as e:
            logger.warning(f"[BookLoreSearch] 搜索失败: {e}")
            return f"书设搜索出错：{e}"

    def _get_lore_conn(self) -> sqlite3.Connection | None:
        """获取书设数据库连接。"""
        if not self.lore_db_path:
            return None
        try:
            return sqlite3.connect(self.lore_db_path)
        except Exception:
            return None


@dataclass
class BookLoreGraphTool(FunctionTool[AstrAgentContext]):
    """书设实体图谱工具：查看实体详情和关系网络。"""

    name: str = "book_lore_graph"
    description: str = (
        "查看书设实体的详细信息和关系网络。"
        "可以查看人物、势力、地点等实体的描述和它们之间的关系。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "entity_name": {
                "type": "string",
                "description": "实体名称（人物/势力/地点等）"
            },
            "depth": {
                "type": "integer",
                "description": "关系遍历深度，默认 2",
                "default": 2
            },
        },
        "required": ["entity_name"],
    })

    db: Any = field(default=None, repr=False)
    lore_db_path: str = field(default="", repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        entity_name = kwargs.get("entity_name", "").strip()
        depth = int(kwargs.get("depth", 2))

        if not entity_name:
            return "请提供实体名称（人物/势力/地点等）"

        conn_lore = self._get_lore_conn()
        if not conn_lore:
            return "书设数据库连接失败"

        try:
            # 查找实体
            entity = conn_lore.execute(
                "SELECT id, title, type, description, frequency, degree FROM book_entities "
                "WHERE title LIKE ? ORDER BY frequency DESC LIMIT 1",
                (f"%{entity_name}%",)
            ).fetchone()

            if not entity:
                return f"没有找到实体「{entity_name}」"

            eid, title, etype, desc, freq, degree = entity
            lines = [f"【{title}】类型:{etype} 频率:{freq} 关系度:{degree}"]
            if desc:
                lines.append(f"描述: {desc[:300]}")

            # 关系网络（第一层）
            limit = 10 * depth
            rels = conn_lore.execute("""
                SELECT source_title, target_title, description, weight
                FROM book_relations
                WHERE source_title = ? OR target_title = ?
                ORDER BY weight DESC LIMIT ?
            """, (title, title, limit)).fetchall()

            if rels:
                lines.append(f"\n关系网络 ({len(rels)} 条):")
                for src, tgt, rdesc, weight in rels:
                    other = tgt if src == title else src
                    direction = "\u2192" if src == title else "\u2190"
                    lines.append(f"  {direction} {other} (w={weight:.0f}): {(rdesc or '')[:60]}")

                # 深度 2：查关联实体的关系
                if depth >= 2:
                    # 获取第一层关联实体
                    first_layer = set()
                    for src, tgt, _, _ in rels[:5]:
                        other = tgt if src == title else src
                        first_layer.add(other)

                    for linked in list(first_layer)[:3]:
                        sub_rels = conn_lore.execute("""
                            SELECT source_title, target_title, description, weight
                            FROM book_relations
                            WHERE (source_title = ? OR target_title = ?) AND source_title != ? AND target_title != ?
                            ORDER BY weight DESC LIMIT 3
                        """, (linked, linked, title, title)).fetchall()
                        if sub_rels:
                            lines.append(f"\n  [{linked}] 的其他关系:")
                            for src, tgt, rdesc, weight in sub_rels:
                                other = tgt if src == linked else src
                                lines.append(f"    \u2192 {other}: {(rdesc or '')[:50]}")

            return "\n".join(lines)

        except Exception as e:
            logger.warning(f"[BookLoreGraph] 查询失败: {e}")
            return f"书设图谱查询出错：{e}"
        finally:
            conn_lore.close()

    def _get_lore_conn(self) -> sqlite3.Connection | None:
        """获取书设数据库连接。"""
        if not self.lore_db_path:
            return None
        try:
            return sqlite3.connect(self.lore_db_path)
        except Exception:
            return None
