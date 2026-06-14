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

    # ═══ 以边为中心构图（修复稀疏图"一坨"问题）═══
    # 先选 top 边 → 再从边端点建节点 → 保证每个节点至少有一条边 → 图有结构

    # Step 4: 实体消歧（name→QQ 合并）
    name_to_qq: dict[str, str] = {}
    qq_to_main: dict[str, str] = {}  # qq → 主名
    try:
        sender_rows = c.db.conn.execute(
            """SELECT sender_name, sender_id, COUNT(*) as cnt FROM memories
               WHERE sender_id != '' AND sender_name != ''
               GROUP BY sender_name, sender_id ORDER BY cnt DESC"""
        ).fetchall()
        for sname, sid, cnt in sender_rows:
            key = sname.strip()[:30]
            name_to_qq[key] = sid
            if sid not in qq_to_main:
                qq_to_main[sid] = key  # 第一个（最高频）作为主名
    except Exception:
        pass

    def resolve_name(n: str) -> str:
        """消歧：同 QQ 的名字合并到主名。"""
        qq = name_to_qq.get(n)
        if qq:
            return qq_to_main.get(qq, n)
        return n

    # Step 5: 构建边（消歧后），按权重排序取 top
    max_edges = max_nodes * 2
    edge_list: list[tuple[str, str, str, float]] = []  # (src, tgt, label, weight)
    seen_pairs: set = set()

    for src, tgt, label, weight in edges_raw:
        src_r = resolve_name(src)
        tgt_r = resolve_name(tgt)
        if src_r == tgt_r:
            continue  # 自环（消歧后同一实体）
        pair = (src_r, tgt_r, label)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        edge_list.append((src_r, tgt_r, label, weight))

    # 按权重排序取 top
    edge_list.sort(key=lambda x: x[3], reverse=True)
    edge_list = edge_list[:max_edges]

    # Step 6: 从边端点构建节点集
    node_degree: dict[str, int] = {}
    for src, tgt, _, _ in edge_list:
        node_degree[src] = node_degree.get(src, 0) + 1
        node_degree[tgt] = node_degree.get(tgt, 0) + 1

    # 限制节点数（优先保留高度数）
    if len(node_degree) > max_nodes:
        sorted_nd = sorted(node_degree.items(), key=lambda x: x[1], reverse=True)[:max_nodes]
        top_set = {n for n, _ in sorted_nd}
        # 过滤边：只保留两端都在 top 里的
        edge_list = [(s, t, l, w) for s, t, l, w in edge_list if s in top_set and t in top_set]
        # 重算度数
        node_degree = {}
        for src, tgt, _, _ in edge_list:
            node_degree[src] = node_degree.get(src, 0) + 1
            node_degree[tgt] = node_degree.get(tgt, 0) + 1
    else:
        top_set = set(node_degree.keys())

    # Step 7: 构建节点
    nodes = []
    name_to_id: dict[str, int] = {}
    for idx, (name, degree) in enumerate(sorted(node_degree.items(), key=lambda x: x[1], reverse=True)):
        nid = idx + 1
        name_to_id[name] = nid
        etype = "person" if name in name_to_qq else entity_type.get(name, "entity")
        nodes.append({"id": nid, "name": name, "type": etype, "degree": degree})

    # Step 8: 构建边（映射到 node id）
    edges = []
    for src, tgt, label, weight in edge_list:
        src_id = name_to_id.get(src)
        tgt_id = name_to_id.get(tgt)
        if src_id and tgt_id:
            edges.append({"source": src_id, "target": tgt_id, "label": label, "weight": round(weight, 2)})

    data = {"nodes": nodes, "edges": edges}
    _overview_cache.update({"version": version, "data": data, "ts": now})
    return jsonify(data)


@kg_bp.route("/entity/<entity_name>")
@require_auth
async def entity_detail(entity_name: str):
    """实体详情：该实体相关的 facts + tag_relations + 关联记忆 + 人物画像(若为人物)。"""
    c = get_container()
    from urllib.parse import unquote
    name = unquote(entity_name).strip()
    limit = int(request.args.get("limit", 15))

    # 人物检测：通过 sender_name 反查 QQ
    person = None
    person_row = c.db.conn.execute(
        "SELECT sender_id, COUNT(*) FROM memories WHERE sender_name = ? AND sender_id != '' GROUP BY sender_id ORDER BY 2 DESC LIMIT 1",
        (name,),
    ).fetchone()
    if person_row:
        qq_id = person_row[0]
        # 聚合所有别名
        aliases = [r[0] for r in c.db.conn.execute(
            "SELECT DISTINCT sender_name FROM memories WHERE sender_id = ? AND sender_name != ''", (qq_id,)
        ).fetchall()]
        msg_count = c.db.conn.execute("SELECT COUNT(*) FROM memories WHERE sender_id = ?", (qq_id,)).fetchone()[0]
        # 好感度 + personality_tags（取最新/最高）
        profile = c.db.conn.execute(
            "SELECT affection, personality_tags, nickname FROM user_profiles WHERE user_id = ? ORDER BY affection DESC LIMIT 1",
            (qq_id,),
        ).fetchone()
        person = {
            "qq_id": qq_id,
            "name": name,
            "aliases": [a for a in aliases if a != name],
            "msg_count": msg_count,
            "affection": profile[0] if profile else 0,
            "personality_tags": json.loads(profile[1] or "[]") if profile and profile[1] else [],
        }

    # 相关 facts（如果是人物，搜所有别名）
    search_names = [name] + (person["aliases"] if person else [])
    facts_all = []
    for n in search_names[:5]:
        rows = c.db.conn.execute(
            "SELECT subject, predicate, object FROM facts WHERE subject = ? OR object = ? LIMIT ?",
            (n, n, limit),
        ).fetchall()
        facts_all.extend(rows)
    # 去重
    seen = set()
    facts = []
    for r in facts_all:
        key = (r[0], r[1], r[2])
        if key not in seen:
            seen.add(key)
            facts.append(r)

    # 相关 tag_relations
    relations_all = []
    for n in search_names[:5]:
        rows = c.db.conn.execute(
            """SELECT t1.name, tr.relation_type, t2.name, tr.weight
               FROM tag_relations tr
               JOIN tags t1 ON tr.source_tag_id = t1.id
               JOIN tags t2 ON tr.target_tag_id = t2.id
               WHERE t1.name = ? OR t2.name = ?
               LIMIT ?""",
            (n, n, limit),
        ).fetchall()
        relations_all.extend(rows)
    relations = list({(r[0],r[1],r[2]): r for r in relations_all}.values())

    # 关联记忆（人物按 QQ 查更准）
    if person:
        memories = c.db.conn.execute(
            "SELECT id, content, sender_name, timestamp FROM memories WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?",
            (person["qq_id"], limit),
        ).fetchall()
    else:
        memories = c.db.conn.execute(
            """SELECT m.id, m.content, m.sender_name, m.timestamp
               FROM memories m JOIN memory_tags mt ON m.id = mt.memory_id JOIN tags t ON mt.tag_id = t.id
               WHERE t.name = ? ORDER BY m.timestamp DESC LIMIT ?""",
            (name, limit),
        ).fetchall()

    return jsonify({
        "name": name,
        "person": person,
        "facts": [{"subject": r[0], "predicate": r[1], "object": r[2]} for r in facts[:limit]],
        "relations": [{"source": r[0], "type": r[1], "target": r[2], "weight": r[3]} for r in relations[:limit]],
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


@kg_bp.route("/entity/<entity_name>/timeline")
@require_auth
async def entity_timeline(entity_name: str):
    """实体时间线：该实体相关的 facts + memories 按时间排列。"""
    c = get_container()
    from urllib.parse import unquote
    name = unquote(entity_name).strip()
    limit = int(request.args.get("limit", 30))

    # 查该实体所有别名（人物消歧）
    names = [name]
    qq_row = c.db.conn.execute(
        "SELECT sender_id FROM memories WHERE sender_name = ? AND sender_id != '' LIMIT 1", (name,)
    ).fetchone()
    if qq_row:
        aliases = c.db.conn.execute(
            "SELECT DISTINCT sender_name FROM memories WHERE sender_id = ? AND sender_name != ''", (qq_row[0],)
        ).fetchall()
        names = list({name} | {a[0] for a in aliases})

    # Facts 时间线
    events = []
    for n in names[:5]:
        rows = c.db.conn.execute(
            "SELECT subject, predicate, object, created_at, source_memory_id FROM facts WHERE (subject=? OR object=?) AND created_at IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (n, n, limit),
        ).fetchall()
        for r in rows:
            events.append({"type": "fact", "ts": r[3], "subject": r[0], "predicate": r[1], "object": r[2], "source_id": r[4]})

    # 关键记忆（按时间）
    if qq_row:
        mem_rows = c.db.conn.execute(
            "SELECT id, content, sender_name, timestamp FROM memories WHERE sender_id = ? ORDER BY timestamp DESC LIMIT ?",
            (qq_row[0], limit),
        ).fetchall()
    else:
        mem_rows = c.db.conn.execute(
            "SELECT m.id, m.content, m.sender_name, m.timestamp FROM memories m JOIN memory_tags mt ON m.id=mt.memory_id JOIN tags t ON mt.tag_id=t.id WHERE t.name=? ORDER BY m.timestamp DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    for r in mem_rows:
        events.append({"type": "memory", "ts": r[3], "id": r[0], "content": r[1], "sender": r[2] or ""})

    # 按时间排序
    events.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    return jsonify({"name": name, "events": events[:limit]})


@kg_bp.route("/path", methods=["POST"])
@require_auth
async def kg_path():
    """多跳路径：两个实体间的最短关系链（BFS on tag_relations + facts）。

    每跳返回关系类型 + 两端实体名，用户能看到 A→关系→B→关系→C 的完整语义链。
    """
    c = get_container()
    body = await request.get_json(silent=True) or {}
    from_name = (body.get("from") or "").strip()
    to_name = (body.get("to") or "").strip()
    max_depth = int(body.get("max_depth", 5))

    if not from_name or not to_name:
        return jsonify({"path": [], "edges": [], "error": "from and to required"})

    # 构建邻接表（name→name，带关系标签）from tag_relations + facts
    adj: dict[str, list[tuple[str, str]]] = {}  # name → [(neighbor, label)]

    # tag_relations
    rel_rows = c.db.conn.execute(
        """SELECT t1.name, tr.relation_type, t2.name FROM tag_relations tr
           JOIN tags t1 ON tr.source_tag_id=t1.id JOIN tags t2 ON tr.target_tag_id=t2.id"""
    ).fetchall()
    for src, rtype, tgt in rel_rows:
        adj.setdefault(src, []).append((tgt, rtype or "relates"))
        adj.setdefault(tgt, []).append((src, rtype or "relates"))

    # facts
    fact_rows = c.db.conn.execute("SELECT subject, predicate, object FROM facts").fetchall()
    for subj, pred, obj in fact_rows:
        if subj and obj:
            adj.setdefault(subj, []).append((obj, pred or "relates"))
            adj.setdefault(obj, []).append((subj, pred or "relates"))

    # BFS
    from collections import deque
    visited: dict[str, tuple] = {from_name: (None, None)}  # name → (parent, edge_label)
    queue = deque([(from_name, 0)])
    found = False

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for neighbor, label in adj.get(current, []):
            if neighbor not in visited:
                visited[neighbor] = (current, label)
                if neighbor == to_name:
                    found = True
                    break
                queue.append((neighbor, depth + 1))
        if found:
            break

    if not found:
        return jsonify({"path": [], "edges": [], "nodes": []})

    # 回溯路径
    path_names = []
    path_edges = []
    node = to_name
    while node is not None:
        path_names.append(node)
        parent, label = visited[node]
        if parent is not None:
            path_edges.append({"source": parent, "target": node, "label": label or "relates"})
        node = parent
    path_names.reverse()
    path_edges.reverse()

    # 构建节点（for graph rendering）
    nodes = [{"id": i+1, "name": n, "type": "entity", "degree": 1} for i, n in enumerate(path_names)]

    return jsonify({"path": path_names, "edges": path_edges, "nodes": nodes})
