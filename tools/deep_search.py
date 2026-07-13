"""Wave Memory DeepSearch Tool — FTS5 全文搜索 + 上下文窗口"""

from __future__ import annotations

from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

try:
    from ..engine.database import WaveMemoryDB
except ImportError:  # 兼容插件顶级加载
    from engine.database import WaveMemoryDB

try:
    from .scope_boundary import (
        extract_group_runtime_scope,
        require_group_runtime_scope,
        scope_error_message,
    )
except ImportError:  # 兼容插件顶级加载
    from tools.scope_boundary import (
        extract_group_runtime_scope,
        require_group_runtime_scope,
        scope_error_message,
    )


def _extract_group_scope(ctx: ContextWrapper):
    """兼容旧私有导入；实际边界统一由 scope_boundary 实现。"""
    return extract_group_runtime_scope(ctx)


@dataclass
class WaveMemoryDeepSearchTool(FunctionTool[AstrAgentContext]):
    """深度搜索工具：FTS5 全文搜索 + 上下文窗口扩展。

    适用于精确关键词搜索、查找特定对话片段、追溯历史事件。
    """

    name: str = "wave_memory_deep_search"
    description: str = (
        "深度搜索历史对话记录。使用关键词精确匹配，并返回匹配消息前后的上下文。"
        "适合查找特定话题、追溯事件经过、确认谁说过什么。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "keywords": {
                "type": "string",
                "description": "搜索关键词，支持多个词用空格分隔（AND 逻辑）"
            },
            "window_size": {
                "type": "integer",
                "description": "上下文窗口大小（命中消息前后各扩展几条），默认 3",
                "default": 3,
            },
            "max_results": {
                "type": "integer",
                "description": "最大返回片段数，默认 5",
                "default": 5,
            },
        },
        "required": ["keywords"],
    })

    db: Any = None

    async def call(self, ctx: ContextWrapper, **kwargs) -> str:
        keywords = kwargs.get("keywords", "").strip()
        window_size = int(kwargs.get("window_size", 3))
        max_results = int(kwargs.get("max_results", 5))

        if not keywords:
            return "请提供搜索关键词。"

        if not self.db:
            return "记忆数据库未初始化。"
        scope, error_code = require_group_runtime_scope(ctx, "memory.message.read")
        if error_code:
            return scope_error_message("深度搜索", error_code)
        assert scope is not None

        # db 存活检测
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "记忆数据库连接异常。"

        try:
            # Hitting IDs and all later window rows must satisfy the exact same
            # Bot/session visibility boundary; FTS itself has no Scope columns.
            base = """
                m.bot_id = ? AND m.session_id = ? AND m.visibility = ?
                AND m.resolution_state = 'resolved' AND m.quarantine = 0
            """
            scope_params = (scope.bot_id, scope.session.id, scope.visibility)
            fts_query = " AND ".join(keywords.split())
            hits = self.db.conn.execute(f"""
                SELECT m.id, rank
                FROM fts_memories
                JOIN memories AS m ON m.id = fts_memories.rowid
                WHERE fts_memories MATCH ? AND {base}
                ORDER BY rank
                LIMIT ?
            """, (fts_query, *scope_params, max_results * 2)).fetchall()

            if not hits:
                # 尝试 OR 搜索，仍只在同一 Scope 内。
                fts_query_or = " OR ".join(keywords.split())
                hits = self.db.conn.execute(f"""
                    SELECT m.id, rank
                    FROM fts_memories
                    JOIN memories AS m ON m.id = fts_memories.rowid
                    WHERE fts_memories MATCH ? AND {base}
                    ORDER BY rank
                    LIMIT ?
                """, (fts_query_or, *scope_params, max_results * 2)).fetchall()

            if not hits:
                return f"未找到包含「{keywords}」的记忆。"

            # 上下文窗口扩展
            fragments = []
            seen_ids = set()

            for hit in hits[:max_results]:
                memory_id = hit[0]
                if memory_id in seen_ids:
                    continue

                # The hit query has already scoped memory_id.  The context
                # window repeats every Scope predicate so adjacent IDs from a
                # different Bot/session can never bleed into the fragment.
                window = self.db.conn.execute("""
                    SELECT id, sender_name, content, timestamp
                    FROM memories
                    WHERE bot_id = ? AND session_id = ? AND visibility = ?
                      AND resolution_state = 'resolved' AND quarantine = 0
                      AND id BETWEEN ? AND ?
                    ORDER BY id ASC
                """, (
                    scope.bot_id,
                    scope.session.id,
                    scope.visibility,
                    memory_id - window_size,
                    memory_id + window_size,
                )).fetchall()

                if not window:
                    continue

                # 格式化片段
                lines = []
                for row in window:
                    mid, sender, content, ts = row
                    seen_ids.add(mid)
                    marker = "→ " if mid == memory_id else "  "
                    sender_str = sender or "unknown"
                    lines.append(f"{marker}{sender_str}: {content}")

                fragments.append("\n".join(lines))

            if not fragments:
                return f"未找到包含「{keywords}」的记忆。"

            # 组装输出
            output_parts = [f"找到 {len(fragments)} 个相关片段：\n"]
            for i, frag in enumerate(fragments, 1):
                output_parts.append(f"[片段{i}]\n{frag}")

            return "\n\n".join(output_parts)

        except Exception as e:
            logger.warning(f"[WaveMemory] DeepSearch failed: {e}")
            return f"搜索出错：{e}"
