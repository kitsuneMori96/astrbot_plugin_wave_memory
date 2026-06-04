"""Wave Memory 扩展工具 — 好感度查询 / 事实查询 / 标签图谱

从 hermes wavememory-mcp 的 mcp_tools.py 适配为 AstrBot FunctionTool 格式。
"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class WaveMemoryAffinityTool(FunctionTool[AstrAgentContext]):
    """查询多维好感度：某人详情、排行榜、黑名单。"""

    name: str = "wave_memory_affinity"
    description: str = (
        "查询好感度信息。可以查某个人的好感度详情，也可以查排行榜或好感度最低的人。"
        "mode=single 查某人，mode=ranking 查排行榜，mode=blacklist 查最低。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "要查询的群友名字或QQ号（mode=single 时必填）"
            },
            "mode": {
                "type": "string",
                "description": "查询模式：single(查某人)/ranking(排行榜)/blacklist(最低)",
                "enum": ["single", "ranking", "blacklist"],
                "default": "single"
            },
        },
        "required": ["user_id"],
    })

    db: Any = field(default=None, repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        user_id = kwargs.get("user_id", "")
        mode = kwargs.get("mode", "single")

        if not self.db:
            return "数据库未初始化"
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "数据库连接已断开"

        conn = self.db.conn

        if mode == "ranking":
            rows = conn.execute("""
                SELECT up.user_id, up.group_id, up.affection, up.metadata, pr.display_name
                FROM user_profiles up
                LEFT JOIN person_registry pr ON up.user_id = pr.qq_id
                WHERE up.metadata IS NOT NULL AND LENGTH(up.metadata) > 10
                ORDER BY up.affection DESC LIMIT 15
            """).fetchall()

            lines = ["【好感度排行 TOP 15】"]
            for uid, gid, aff, meta_str, dname in rows:
                display = dname or uid
                meta = json.loads(meta_str) if meta_str else {}
                impression = meta.get("impression", "")[:40]
                gid_short = gid[-4:] if gid else "?"
                lines.append(f"  {display}(群{gid_short}): {aff} — {impression}")
            return "\n".join(lines) if len(lines) > 1 else "没有好感度记录"

        elif mode == "blacklist":
            rows = conn.execute("""
                SELECT up.user_id, up.group_id, up.affection, up.metadata, pr.display_name
                FROM user_profiles up
                LEFT JOIN person_registry pr ON up.user_id = pr.qq_id
                ORDER BY up.affection ASC LIMIT 10
            """).fetchall()

            lines = ["【好感度最低 10 人】"]
            for uid, gid, aff, meta_str, dname in rows:
                display = dname or uid
                meta = json.loads(meta_str) if meta_str else {}
                impression = meta.get("impression", "")[:40]
                gid_short = gid[-4:] if gid else "?"
                lines.append(f"  {display}(群{gid_short}): {aff} — {impression}")
            return "\n".join(lines) if len(lines) > 1 else "没有好感度记录"

        else:
            # single 模式
            if not user_id:
                return "请提供群友名字或QQ号，或使用 mode=ranking 查看排行"

            rows = conn.execute("""
                SELECT up.user_id, up.group_id, up.affection, up.metadata, pr.display_name
                FROM user_profiles up
                LEFT JOIN person_registry pr ON up.user_id = pr.qq_id
                WHERE pr.display_name LIKE ? OR up.user_id = ? OR pr.aliases LIKE ?
            """, (f"%{user_id}%", user_id, f"%{user_id}%")).fetchall()

            if not rows:
                return f"没有找到「{user_id}」的好感度记录"

            lines = []
            for uid, gid, aff, meta_str, dname in rows:
                display = dname or uid
                lines.append(f"【{display}】群{gid} 好感度: {aff}")
                if meta_str:
                    meta = json.loads(meta_str)
                    if meta.get("impression"):
                        lines.append(f"  印象: {meta['impression']}")
                    if meta.get("tags"):
                        top = sorted(meta["tags"].items(), key=lambda x: x[1], reverse=True)[:10]
                        lines.append(f"  标签: {', '.join(f'{k}({v})' for k, v in top)}")
                    if meta.get("meta_updated"):
                        lines.append(f"  更新: {meta['meta_updated']}")
            return "\n".join(lines)


@dataclass
class WaveMemoryFactsTool(FunctionTool[AstrAgentContext]):
    """查询事实三元组（知识图谱）。"""

    name: str = "wave_memory_facts"
    description: str = (
        "搜索记忆中的事实三元组（知识图谱）。"
        "可以用关键词搜索相关的 主体→关系→客体 知识。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，会匹配主体、谓语、客体"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量，默认 10",
                "default": 10
            },
        },
        "required": ["query"],
    })

    db: Any = field(default=None, repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        query = kwargs.get("query", "")
        limit = int(kwargs.get("limit", 10))

        if not query:
            return "请提供搜索关键词"

        if not self.db:
            return "数据库未初始化"
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "数据库连接已断开"

        conn = self.db.conn

        rows = conn.execute(
            "SELECT subject, predicate, object, confidence, group_id FROM facts "
            "WHERE subject LIKE ? OR object LIKE ? OR predicate LIKE ? "
            "ORDER BY confidence DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", f"%{query}%", limit)
        ).fetchall()

        if not rows:
            return f"没有找到与「{query}」相关的事实"

        lines = [f"找到 {len(rows)} 条事实:"]
        for subj, pred, obj, conf, gid in rows:
            lines.append(f"  {subj} → {pred} → {obj} (置信度:{conf:.1f})")
        return "\n".join(lines)


@dataclass
class WaveMemoryTagGraphTool(FunctionTool[AstrAgentContext]):
    """查询标签共现关系图。"""

    name: str = "wave_memory_tag_graph"
    description: str = (
        "查询标签的关系网络。提供标签名可以查看它的关联标签和相关记忆。"
        "不提供标签名则返回热门标签列表。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "tag_name": {
                "type": "string",
                "description": "要查询的标签名（留空则返回热门标签）"
            },
        },
        "required": ["tag_name"],
    })

    db: Any = field(default=None, repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        tag_name = kwargs.get("tag_name", "")

        if not self.db:
            return "数据库未初始化"
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "数据库连接已断开"

        conn = self.db.conn

        if not tag_name:
            # 返回热门标签
            rows = conn.execute("""
                SELECT t.name, t.tag_type, t.frequency, COUNT(mt.memory_id) as mc
                FROM tags t JOIN memory_tags mt ON t.id = mt.tag_id
                GROUP BY t.id
                ORDER BY mc DESC LIMIT 20
            """).fetchall()
            if not rows:
                return "没有标签数据"
            lines = ["【热门标签 TOP 20】"]
            for name, ttype, freq, mc in rows:
                lines.append(f"  #{name}({ttype}) — 关联{mc}条记忆")
            return "\n".join(lines)

        # 查找指定 tag 的关系网络
        tag = conn.execute(
            "SELECT id, name, tag_type, frequency FROM tags WHERE name LIKE ?",
            (f"%{tag_name}%",)
        ).fetchone()
        if not tag:
            return f"没有找到标签「{tag_name}」"

        tid, tname, ttype, freq = tag
        lines = [f"【#{tname}】类型:{ttype} 频率:{freq}"]

        # 指向的关系
        rels = conn.execute("""
            SELECT t2.name, tr.relation_type, tr.weight, tr.confidence
            FROM tag_relations tr
            JOIN tags t2 ON tr.target_tag_id = t2.id
            WHERE tr.source_tag_id = ?
            ORDER BY tr.weight DESC LIMIT 10
        """, (tid,)).fetchall()

        if rels:
            lines.append(f"\n指向的关系:")
            for name, rtype, weight, conf in rels:
                lines.append(f"  → #{name} [{rtype}] (强度:{weight:.0f}, 置信:{conf:.1f})")

        # 被指向
        rels_in = conn.execute("""
            SELECT t1.name, tr.relation_type, tr.weight
            FROM tag_relations tr
            JOIN tags t1 ON tr.source_tag_id = t1.id
            WHERE tr.target_tag_id = ?
            ORDER BY tr.weight DESC LIMIT 10
        """, (tid,)).fetchall()

        if rels_in:
            lines.append(f"\n被关联:")
            for name, rtype, weight in rels_in:
                lines.append(f"  ← #{name} [{rtype}] (强度:{weight:.0f})")

        # 共现的记忆片段
        mem_samples = conn.execute("""
            SELECT m.sender_name, m.content
            FROM memory_tags mt JOIN memories m ON mt.memory_id = m.id
            WHERE mt.tag_id = ?
            ORDER BY m.timestamp DESC LIMIT 3
        """, (tid,)).fetchall()
        if mem_samples:
            lines.append(f"\n近期相关记忆:")
            for sender, content in mem_samples:
                lines.append(f"  [{sender}] {(content or '')[:80]}")

        return "\n".join(lines)
