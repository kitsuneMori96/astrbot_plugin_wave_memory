"""Wave Memory 扩展工具 — 好感度查询 / 事实查询 / 标签图谱

从 hermes wavememory-mcp 的 mcp_tools.py 适配为 AstrBot FunctionTool 格式。
"""

from __future__ import annotations

import json
import time
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext


@dataclass
class WaveMemoryAffinityTool(FunctionTool[AstrAgentContext]):
    """查询社交关系：互动排行、活跃用户、某人画像。"""

    name: str = "wave_memory_affinity"
    description: str = (
        "查询社交信息。mode=ranking 查互动排行（谁和你聊最多），"
        "mode=active 查最近活跃用户，mode=single 查某人的互动信息。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "user_id": {
                "type": "string",
                "description": "要查询的群友名字或QQ号（mode=single 时需要）"
            },
            "mode": {
                "type": "string",
                "description": "查询模式：single(查某人)/ranking(互动排行)/active(最近活跃)",
                "enum": ["single", "ranking", "active"],
                "default": "ranking"
            },
            "scope": {
                "type": "string",
                "description": "群范围：current_group(当前群或指定group_id)/global(全部群合并)/all_groups(按群分别展示)",
                "enum": ["current_group", "global", "all_groups"],
                "default": "global"
            },
            "group_id": {
                "type": "string",
                "description": "指定群号；传入后 scope=current_group 会分析这个群，而不是只能依赖当前上下文"
            },
            "bot_scope": {
                "type": "string",
                "description": "bot 范围：current_bot(当前bot)/all_bots(全部bot)。也可直接传 bot_id 指定某个bot人格",
                "enum": ["current_bot", "all_bots"],
                "default": "current_bot"
            },
            "bot_id": {
                "type": "string",
                "description": "指定 bot 的 db_id，例如 yushu 或 baizz；传入后只查询该 bot 的好感度画像"
            },
        },
        "required": [],
    })

    db: Any = field(default=None, repr=False)
    bot_db_ids: dict[str, str] = field(default_factory=dict, repr=False)

    async def call(self, ctx: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        user_id = kwargs.get("user_id", "")
        mode = kwargs.get("mode", "single")
        scope = kwargs.get("scope", "global") or "global"
        requested_group_id = kwargs.get("group_id", "") or ""
        requested_bot_id = kwargs.get("bot_id", "") or ""
        bot_scope = kwargs.get("bot_scope", "current_bot") or "current_bot"
        inner_ctx = getattr(ctx, "context", None)
        event = getattr(inner_ctx, "event", None)
        current_group_id = requested_group_id or (event.get_group_id() if event else "")
        self_id = event.get_self_id() if event else ""
        current_bot_id = self.bot_db_ids.get(self_id)
        selected_bot_id = requested_bot_id or current_bot_id
        include_all_bots = (bot_scope == "all_bots" and not requested_bot_id) or not selected_bot_id

        if not self.db:
            return "数据库未初始化"
        if self.db.closed:
            try:
                self.db.reopen()
            except Exception:
                return "数据库连接已断开"

        conn = self.db.conn

        if mode == "ranking":
            where = [
                "up.interaction_count > 0",
                "COALESCE(up.metadata, '') NOT LIKE '%legacy_neutral%'",
                "COALESCE(up.metadata, '') NOT LIKE '%legacy_unverified%'",
            ]
            params = []
            if not include_all_bots:
                where.append("up.bot_id = ?")
                params.append(selected_bot_id)
            if scope == "current_group":
                if not current_group_id:
                    return "当前不是群聊上下文，无法查询当前群排行"
                where.append("up.group_id = ?")
                params.append(current_group_id)
            group_select = "up.group_id," if scope == "all_groups" else ""
            bot_select = "up.bot_id," if include_all_bots else ""
            group_order = "up.group_id ASC," if scope == "all_groups" else ""
            bot_order = "up.bot_id ASC," if include_all_bots else ""
            rows = conn.execute(f"""
                SELECT {group_select} {bot_select} up.user_id, COALESCE(NULLIF(up.nickname, ''), pr.display_name), up.interaction_count, up.metadata,
                       (SELECT COUNT(*) FROM memories m
                        WHERE m.sender_id = up.user_id
                          AND (? != 'current_group' OR m.group_id = up.group_id)) as msg_count
                FROM user_profiles up
                LEFT JOIN person_registry pr ON up.user_id = pr.qq_id
                WHERE {' AND '.join(where)}
                ORDER BY {group_order} {bot_order} up.interaction_count DESC LIMIT 30
            """, [scope] + params).fetchall()

            title = "当前群" if scope == "current_group" else "全部群" if scope == "global" else "按群"
            bot_title = "全部bot" if include_all_bots else f"bot={selected_bot_id}"
            lines = [f"【互动排行 TOP 10｜{title}｜{bot_title}】"]
            for row in rows[:10] if scope != "all_groups" else rows:
                idx = 0
                prefix_parts = []
                if scope == "all_groups":
                    gid = row[idx]
                    idx += 1
                    prefix_parts.append(f"群{gid}")
                if include_all_bots:
                    row_bot_id = row[idx]
                    idx += 1
                    prefix_parts.append(f"bot={row_bot_id}")
                uid, nickname, interactions, meta_str, msg_count = row[idx:idx + 5]
                display = nickname or uid
                try:
                    row_meta = json.loads(meta_str) if meta_str else {}
                except Exception:
                    row_meta = {}
                if row_meta.get("target_type") == "bot":
                    display = f"{row_meta.get('target_name') or display}(bot)"
                prefix = (" ".join(prefix_parts) + " ") if prefix_parts else ""
                lines.append(f"  {prefix}{display}: 互动{interactions}次, 消息{msg_count}条")
            return "\n".join(lines) if len(lines) > 1 else "还没有互动记录"

        elif mode == "active":
            where = ["timestamp > ?", "sender_id != 'bot'", "sender_name != ''"]
            params = [time.time() - 7 * 86400]
            if scope == "current_group":
                if not current_group_id:
                    return "当前不是群聊上下文，无法查询当前群活跃用户"
                where.append("group_id = ?")
                params.append(current_group_id)
            group_select = "group_id," if scope == "all_groups" else ""
            group_by = "group_id, sender_id" if scope == "all_groups" else "sender_id"
            group_order = "group_id ASC," if scope == "all_groups" else ""
            rows = conn.execute(f"""
                SELECT {group_select} sender_name, sender_id, COUNT(*) as cnt
                FROM memories
                WHERE {' AND '.join(where)}
                GROUP BY {group_by}
                ORDER BY {group_order} cnt DESC LIMIT 30
            """, params).fetchall()

            title = "当前群" if scope == "current_group" else "全部群" if scope == "global" else "按群"
            lines = [f"【最近7天活跃 TOP 10｜{title}】"]
            for row in rows[:10] if scope != "all_groups" else rows:
                if scope == "all_groups":
                    gid, name, uid, cnt = row
                    prefix = f"群{gid} "
                else:
                    name, uid, cnt = row
                    prefix = ""
                lines.append(f"  {prefix}{name}: {cnt} 条消息")
            return "\n".join(lines) if len(lines) > 1 else "最近7天无活跃数据"

        else:
            # single 模式
            if not user_id:
                return "请提供群友名字或QQ号，或使用 mode=ranking 查看排行"

            where = [
                "(pr.display_name LIKE ? OR up.user_id = ? OR pr.aliases LIKE ?)",
                "COALESCE(up.metadata, '') NOT LIKE '%legacy_neutral%'",
                "COALESCE(up.metadata, '') NOT LIKE '%legacy_unverified%'",
            ]
            params = [f"%{user_id}%", user_id, f"%{user_id}%"]
            if not include_all_bots:
                where.append("up.bot_id = ?")
                params.append(selected_bot_id)
            if scope == "current_group":
                if not current_group_id:
                    return "当前不是群聊上下文，无法查询当前群用户"
                where.append("up.group_id = ?")
                params.append(current_group_id)
            rows = conn.execute(f"""
                SELECT up.user_id, up.group_id, up.bot_id, up.affection, up.metadata, pr.display_name
                FROM user_profiles up
                LEFT JOIN person_registry pr ON up.user_id = pr.qq_id
                WHERE {' AND '.join(where)}
                ORDER BY CASE WHEN up.group_id = ? THEN 0 ELSE 1 END, up.bot_id ASC, up.last_seen DESC
            """, params + [current_group_id]).fetchall()

            if not rows:
                return f"没有找到「{user_id}」的好感度记录"

            lines = []
            for uid, gid, row_bot_id, aff, meta_str, dname in rows:
                display = dname or uid
                meta = {}
                if meta_str:
                    meta = json.loads(meta_str)
                if meta.get("target_type") == "bot":
                    display = f"{meta.get('target_name') or display}(bot)"
                prefix = "当前群" if gid == current_group_id else "其他群"
                lines.append(f"【{display}】{prefix} {gid} bot={row_bot_id} 好感度: {aff}")
                if meta:
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
