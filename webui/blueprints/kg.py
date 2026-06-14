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

    # Step 4: 实体消歧 — 同 QQ 不同名合并(斯扎拉克=山东把妹王=苦海天尊 → 一个节点)
    # 构建 name→QQ 映射(从 top 发言者)
    name_to_qq: dict[str, str] = {}
    try:
        sender_rows = c.db.conn.execute(
            """SELECT sender_name, sender_id FROM memories
               WHERE sender_id != '' AND sender_name != ''
               GROUP BY sender_name, sender_id"""
        ).fetchall()
        for sname, sid in sender_rows:
            name_to_qq[sname.strip()[:30]] = sid
    except Exception:
        pass

    # 合并同 QQ 的实体度数
    qq_merged: dict[str, dict] = {}  # qq → {name: 主名, degree: 合并度数, type, aliases}
    standalone_entities: dict[str, tuple] = {}  # name → (degree, type)

    for name, degree in entity_degree.items():
        qq = name_to_qq.get(name)
        if qq:
            if qq not in qq_merged:
                qq_merged[qq] = {"name": name, "degree": degree, "type": "person", "aliases": []}
            else:
                qq_merged[qq]["degree"] += degree
                qq_merged[qq]["aliases"].append(name)
                # 保留度数最高的名字做主名
                if degree > entity_degree.get(qq_merged[qq]["name"], 0):
                    qq_merged[qq]["aliases"].append(qq_merged[qq]["name"])
                    qq_merged[qq]["name"] = name
        else:
            standalone_entities[name] = (degree, entity_type.get(name, "entity"))

    # 合并后的实体列表
    merged_list: list[tuple[str, int, str]] = []  # (name, degree, type)
    for qq_data in qq_merged.values():
        merged_list.append((qq_data["name"], qq_data["degree"], "person"))
    for name, (degree, etype) in standalone_entities.items():
        merged_list.append((name, degree, etype))

    # 取 top N
    merged_list.sort(key=lambda x: x[1], reverse=True)
    top_entities = merged_list[:max_nodes]
    top_set = {name for name, _, _ in top_entities}

    # Step 5: 构建节点
    nodes = []
    name_to_id: dict[str, int] = {}
    for idx, (name, degree, etype) in enumerate(top_entities):
        nid = idx + 1
        name_to_id[name] = nid
        nodes.append({"id": nid, "name": name, "type": etype, "degree": degree})

    # 把消歧别名也映射到同一 id（让边能正确连接）
    for qq_data in qq_merged.values():
        main_name = qq_data["name"]
        if main_name in name_to_id:
            nid = name_to_id[main_name]
            for alias in qq_data["aliases"]:
                name_to_id[alias] = nid
                top_set.add(alias)  # 别名也算在 top_set 里,让边能通过

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
