"""KnowledgeGraph Blueprint — 统一知识图谱查询层 (M1)

从 facts + tag_relations 聚合语义图谱，替代 cooccurrence 统计共现。
不改底层表结构，纯查询层虚拟图。
"""

from __future__ import annotations

import json
import time

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

kg_bp = Blueprint("kg", __name__, url_prefix="/api/kg")

# 全景图缓存（按 facts+relations 行数版本缓存）
_overview_cache: dict = {"version": None, "data": None, "ts": 0}
_CACHE_TTL = 120  # 2 分钟


@kg_bp.route("/overview")
@require_auth
async def overview():
    """全景知识图谱：从 facts + tag_relations 聚合 top 实体和语义边。

    返回 {nodes: [{id, name, type, degree}], edges: [{source, target, label, weight}]}
    """
    c = get_container()
    max_nodes = int(request.args.get("max_nodes", 150))
    max_nodes = max(50, min(500, max_nodes))

    # 版本缓存
    now = time.time()
    try:
        version = c.db.conn.execute(
            "SELECT (SELECT COUNT(*) FROM facts) + (SELECT COUNT(*) FROM tag_relations)"
        ).fetchone()[0]
    except Exception:
        version = 0
    if _overview_cache["version"] == version and _overview_cache["data"] and (now - _overview_cache["ts"]) < _CACHE_TTL:
        return jsonify(_overview_cache["data"])

    # Step 1: 从 facts 提取实体和边
    fact_rows = c.db.conn.execute(
        """SELECT subject, predicate, object, confidence
           FROM facts ORDER BY confidence DESC LIMIT 2000"""
    ).fetchall()

    # Step 2: 从 tag_relations 提取实体和边
    rel_rows = c.db.conn.execute(
        """SELECT t1.name, tr.relation_type, t2.name, tr.weight, t1.tag_type, t2.tag_type
           FROM tag_relations tr
           JOIN tags t1 ON tr.source_tag_id = t1.id
           JOIN tags t2 ON tr.target_tag_id = t2.id
           WHERE tr.weight >= 1.0
           ORDER BY tr.weight DESC LIMIT 2000"""
    ).fetchall()

    # Step 3: 构建实体度数表
    entity_degree: dict[str, int] = {}
    entity_type: dict[str, str] = {}
    edges_raw: list[tuple] = []

    for subj, pred, obj, conf in fact_rows:
        if not subj or not obj:
            continue
        subj = subj.strip()[:30]
        obj = obj.strip()[:30]
        entity_degree[subj] = entity_degree.get(subj, 0) + 1
        entity_degree[obj] = entity_degree.get(obj, 0) + 1
        entity_type.setdefault(subj, "entity")
        entity_type.setdefault(obj, "entity")
        edges_raw.append((subj, obj, pred.strip()[:20] if pred else "relates", float(conf or 1)))

    for src_name, rel_type, tgt_name, weight, src_type, tgt_type in rel_rows:
        if not src_name or not tgt_name:
            continue
        src_name = src_name.strip()[:30]
        tgt_name = tgt_name.strip()[:30]
        entity_degree[src_name] = entity_degree.get(src_name, 0) + 1
        entity_degree[tgt_name] = entity_degree.get(tgt_name, 0) + 1
        entity_type.setdefault(src_name, src_type or "topic")
        entity_type.setdefault(tgt_name, tgt_type or "topic")
        edges_raw.append((src_name, tgt_name, rel_type or "relates", float(weight or 1)))

    # Step 4: 取 top N 高连接实体
    sorted_entities = sorted(entity_degree.items(), key=lambda x: x[1], reverse=True)[:max_nodes]
    top_set = {name for name, _ in sorted_entities}

    # Step 5: 构建节点
    nodes = []
    name_to_id: dict[str, int] = {}
    for idx, (name, degree) in enumerate(sorted_entities):
        nid = idx + 1
        name_to_id[name] = nid
        etype = entity_type.get(name, "entity")
        # 人物检测：如果该名字在 person 类 tag 或在 memories.sender_name 里
        if etype == "person" or etype == "entity":
            # 简单启发式：出现在 tag_relations 的 source 且度数高 → 可能是人
            pass
        nodes.append({"id": nid, "name": name, "type": etype, "degree": degree})

    # Step 6: 筛选边（两端都在 top N 里）+ 去重
    edges = []
    seen_edges: set = set()
    for src, tgt, label, weight in edges_raw:
        if src not in top_set or tgt not in top_set:
            continue
        src_id = name_to_id[src]
        tgt_id = name_to_id[tgt]
        edge_key = (src_id, tgt_id, label)
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append({"source": src_id, "target": tgt_id, "label": label, "weight": round(weight, 2)})

    # 限制边数避免过密
    edges = sorted(edges, key=lambda e: e["weight"], reverse=True)[:max_nodes * 3]

    data = {"nodes": nodes, "edges": edges}
    _overview_cache.update({"version": version, "data": data, "ts": now})
    return jsonify(data)


@kg_bp.route("/entity/<entity_name>")
@require_auth
async def entity_detail(entity_name: str):
    """实体详情：该实体相关的 facts + tag_relations + 关联记忆。"""
    c = get_container()
    from urllib.parse import unquote
    name = unquote(entity_name).strip()
    limit = int(request.args.get("limit", 15))

    # 相关 facts
    facts = c.db.conn.execute(
        "SELECT subject, predicate, object FROM facts WHERE subject = ? OR object = ? LIMIT ?",
        (name, name, limit),
    ).fetchall()

    # 相关 tag_relations
    relations = c.db.conn.execute(
        """SELECT t1.name, tr.relation_type, t2.name, tr.weight
           FROM tag_relations tr
           JOIN tags t1 ON tr.source_tag_id = t1.id
           JOIN tags t2 ON tr.target_tag_id = t2.id
           WHERE t1.name = ? OR t2.name = ?
           LIMIT ?""",
        (name, name, limit),
    ).fetchall()

    # 关联记忆（通过 tags → memory_tags → memories）
    memories = c.db.conn.execute(
        """SELECT m.id, m.content, m.sender_name, m.timestamp
           FROM memories m
           JOIN memory_tags mt ON m.id = mt.memory_id
           JOIN tags t ON mt.tag_id = t.id
           WHERE t.name = ?
           ORDER BY m.timestamp DESC LIMIT ?""",
        (name, limit),
    ).fetchall()

    return jsonify({
        "name": name,
        "facts": [{"subject": r[0], "predicate": r[1], "object": r[2]} for r in facts],
        "relations": [{"source": r[0], "type": r[1], "target": r[2], "weight": r[3]} for r in relations],
        "memories": [{"id": r[0], "content": r[1], "sender": r[2] or "", "ts": r[3]} for r in memories],
    })


@kg_bp.route("/stats")
@require_auth
async def kg_stats():
    """图谱统计。"""
    c = get_container()
    return jsonify({
        "facts": c.db.conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0],
        "tag_relations": c.db.conn.execute("SELECT COUNT(*) FROM tag_relations").fetchone()[0],
        "beliefs": c.db.conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0],
        "concerns": c.db.conn.execute("SELECT COUNT(*) FROM concerns").fetchone()[0],
        "jargon": c.db.conn.execute("SELECT COUNT(*) FROM jargon WHERE is_jargon=1").fetchone()[0],
        "persons": c.db.conn.execute("SELECT COUNT(DISTINCT sender_id) FROM memories WHERE sender_id!=''").fetchone()[0],
    })
