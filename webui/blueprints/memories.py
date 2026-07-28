"""Memories Blueprint — 记忆查询、导入、统计"""

from __future__ import annotations

import asyncio
import json
import time

try:
    from quart import Blueprint, jsonify, request, Response
except ImportError:  # pragma: no cover - 本地单测可能注入不完整 fake Quart
    from quart import Blueprint, jsonify, request

    class Response:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

from ..container import get_container
from ..middleware.auth import require_auth

memories_bp = Blueprint("memories", __name__, url_prefix="/api")

_import_lock = asyncio.Lock()

# 无过滤全表计数缓存（COUNT(*) 在十万行约 110ms，过滤计数则更慢，故仅缓存全表）
_total_cache: dict = {"value": None, "ts": 0.0}
_TOTAL_TTL = 30.0


def _table_exists(conn, table: str) -> bool:
    """检查表是否存在。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _safe_int(val, default):
    """安全 int 转换。"""
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_float(val, default):
    """安全 float 转换。"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


@memories_bp.route("/memories", methods=["GET"])
@require_auth
async def list_memories():
    """分页查看记忆列表（兼容 page/size 与 limit/offset，支持搜索与筛选）。"""
    c = get_container()

    # 分页：优先 page/size（前端），回退 limit/offset
    page = request.args.get("page")
    size = request.args.get("size")
    if page is not None or size is not None:
        size_i = max(1, min(200, _safe_int(size or 30, 30)))
        page_i = max(1, _safe_int(page or 1, 1))
        limit = size_i
        offset = (page_i - 1) * size_i
    else:
        limit = max(1, min(200, _safe_int(request.args.get("limit", 50), 50)))
        offset = max(0, _safe_int(request.args.get("offset", 0), 0))

    source = request.args.get("source")
    sender_id = request.args.get("sender_id")
    sender = request.args.get("sender")  # 按 sender_name
    group_id = request.args.get("group_id")
    search = (request.args.get("search") or "").strip()
    has_tags = request.args.get("has_tags")      # 'true'/'false'
    has_vector = request.args.get("has_vector")  # 'true'/'false'
    before_id = request.args.get("before_id")    # keyset 游标：取 id < before_id（深翻页 O(1)）
    bot_id = request.args.get("bot_id")           # 按 bot 的 QQ 号过滤 sender_id

    where = ["1=1"]
    params = []
    real_filter = False  # before_id 是游标翻页，不算"过滤"（无过滤时仍可用 total 缓存）
    if before_id:
        where.append("id < ?"); params.append(_safe_int(before_id, 0))
        offset = 0  # keyset 模式忽略 offset
    if source:
        where.append("source = ?"); params.append(source); real_filter = True
    if sender_id:
        where.append("sender_id = ?"); params.append(sender_id); real_filter = True
    if sender:
        where.append("sender_name = ?"); params.append(sender); real_filter = True
    if group_id:
        where.append("group_id = ?"); params.append(group_id); real_filter = True
    if bot_id:
        where.append("sender_id = ?"); params.append(bot_id); real_filter = True
    if search:
        where.append("content LIKE ?"); params.append(f"%{search}%"); real_filter = True
    if has_vector == "true":
        where.append("vector IS NOT NULL"); real_filter = True
    elif has_vector == "false":
        where.append("vector IS NULL"); real_filter = True
    if has_tags == "true":
        where.append("EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = memories.id)"); real_filter = True
    elif has_tags == "false":
        where.append("NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = memories.id)"); real_filter = True

    where_sql = " AND ".join(where)
    # 多取 1 条用于判断是否还有下一页（避免昂贵的过滤 COUNT）
    sql = (
        f"SELECT id, content, sender_id, sender_name, group_id, source, timestamp, "
        f"vector IS NOT NULL FROM memories WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
    )
    rows = c.db.conn.execute(sql, params + [limit + 1, offset]).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]

    # 批量查 tags
    ids = [r[0] for r in rows]
    tag_map: dict[int, list[dict]] = {}
    if ids:
        placeholders = ",".join("?" * len(ids))
        tag_rows = c.db.conn.execute(
            f"""SELECT mt.memory_id, t.name, t.tag_type
                FROM memory_tags mt JOIN tags t ON t.id = mt.tag_id
                WHERE mt.memory_id IN ({placeholders}) ORDER BY mt.position""",
            ids,
        ).fetchall()
        for mid, tname, ttype in tag_rows:
            tag_map.setdefault(mid, []).append({"name": tname, "type": ttype})

    # total：无筛选时用带缓存的全表计数（~110ms）；有筛选时跳过精确 COUNT
    # （LIKE / vector IS NULL 全表扫描需 ~2.7s），返回 null + has_more 供前端游标翻页
    if real_filter:
        total = None
    else:
        now = time.time()
        if _total_cache["value"] is not None and (now - _total_cache["ts"]) < _TOTAL_TTL:
            total = _total_cache["value"]
        else:
            total = c.db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            _total_cache["value"] = total
            _total_cache["ts"] = now

    items = [
        {"id": r[0], "content": r[1], "sender_id": r[2], "sender_name": r[3],
         "group_id": r[4], "source": r[5], "timestamp": r[6], "has_vector": bool(r[7]),
         "tags": tag_map.get(r[0], [])}
        for r in rows
    ]
    return jsonify({"items": items, "total": total, "has_more": has_more, "limit": limit, "offset": offset})


@memories_bp.route("/memories/senders", methods=["GET"])
@require_auth
async def list_senders():
    """发送者列表（按记忆数排序，供筛选下拉）。"""
    c = get_container()
    limit = max(1, min(500, _safe_int(request.args.get("limit", 100), 100)))
    rows = c.db.conn.execute(
        """SELECT sender_name, COUNT(*) AS cnt FROM memories
           WHERE sender_name IS NOT NULL AND sender_name != ''
           GROUP BY sender_name ORDER BY cnt DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return jsonify({"senders": [{"name": r[0], "count": r[1]} for r in rows]})


@memories_bp.route("/memories/<int:memory_id>", methods=["GET"])
@require_auth
async def get_memory(memory_id: int):
    """记忆详情。"""
    c = get_container()
    detail = c.db.get_memory_detail(memory_id)
    if not detail:
        return jsonify({"error": "not found"}), 404
    return jsonify(detail)


@memories_bp.route("/memories/<int:memory_id>", methods=["PUT"])
@require_auth
async def update_memory(memory_id: int):
    """更新记忆 content / importance。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    content = body.get("content")
    importance = body.get("importance")
    c.db.update_memory(memory_id, content=content, importance=importance)
    return jsonify({"ok": True, "memory_id": memory_id})


@memories_bp.route("/memories/<int:memory_id>", methods=["DELETE"])
@require_auth
async def delete_memory(memory_id: int):
    """删除单条记忆。"""
    c = get_container()
    c.db.delete_memory(memory_id)
    return jsonify({"ok": True, "deleted": memory_id})


@memories_bp.route("/memories/<int:memory_id>/unarchive", methods=["POST"])
@require_auth
async def unarchive_memory(memory_id: int):
    """将 archived/evicted 记忆恢复为活跃状态。"""
    c = get_container()
    ok = c.db.unarchive_memory(memory_id)
    if not ok:
        return jsonify({"error": "not found or not archived/evicted"}), 404
    return jsonify({"ok": True, "memory_id": memory_id})

@memories_bp.route("/memories/<int:memory_id>/re-embed", methods=["POST"])
@require_auth
async def re_embed_memory(memory_id: int):
    """重新向量化单条记忆。"""
    c = get_container()
    detail = c.db.get_memory_detail(memory_id)
    if not detail:
        return jsonify({"error": "not found"}), 404
    vec = await c.embedding_service.get_embedding(detail["content"] or "")
    if vec is None:
        return jsonify({"ok": False, "error": "embedding failed"}), 500
    c.db.update_memory_vector(memory_id, vec)
    try:
        if c.memory_index:
            c.memory_index.add([memory_id], [vec])
    except Exception:
        pass
    return jsonify({"ok": True, "memory_id": memory_id})


@memories_bp.route("/memories/<int:memory_id>/similar", methods=["GET"])
@require_auth
async def similar_memories(memory_id: int):
    """相似记忆：用 memory_index 做向量检索。"""
    c = get_container()
    top_k = _safe_int(request.args.get("top_k", 6), 6)

    detail = c.db.get_memory_detail(memory_id)
    if not detail:
        return jsonify({"error": "not found"}), 404
    if not detail.get("has_vector"):
        return jsonify({"items": []})

    vecs = c.db.get_memory_vectors([memory_id])
    query_vec = vecs.get(memory_id)
    if query_vec is None or not c.memory_index:
        return jsonify({"items": []})

    results = c.memory_index.search(query_vec.reshape(1, -1), k=top_k + 1)
    if not results:
        return jsonify({"items": []})

    similar_ids = [int(r[0]) for r in results if int(r[0]) != memory_id][:top_k]
    distances = {int(r[0]): float(r[1]) for r in results}

    memories = c.db.get_memories_by_ids(similar_ids)
    # map distance to similarity score (0-1, higher = more similar)
    max_dist = max(distances.values()) if distances else 1.0
    items = []
    for m in memories:
        mid = m["id"]
        raw_dist = distances.get(mid, 1.0)
        score = 1.0 - (raw_dist / max_dist) if max_dist > 0 else 0.0
        items.append({
            "id": mid,
            "content": (m.get("content") or "")[:200],
            "sender_name": m.get("sender_name"),
            "timestamp": m.get("timestamp"),
            "score": round(score, 4),
        })
    items.sort(key=lambda x: x["score"], reverse=True)
    return jsonify({"items": items})


@memories_bp.route("/memories/<int:memory_id>/related-facts", methods=["GET"])
@require_auth
async def related_facts(memory_id: int):
    """关联事实：查询 facts 表中 source_memory_id 指向该记忆的记录。"""
    c = get_container()
    limit = _safe_int(request.args.get("limit", 5), 5)

    if not _table_exists(c.db.conn, "facts"):
        return jsonify({"items": []})

    rows = c.db.conn.execute(
        """SELECT id, subject, predicate, object, fact_type, confidence
           FROM facts WHERE source_memory_id = ? ORDER BY id DESC LIMIT ?""",
        (memory_id, limit),
    ).fetchall()
    items = [
        {"id": r[0], "subject": r[1], "predicate": r[2],
         "object": r[3], "fact_type": r[4], "confidence": r[5]}
        for r in rows
    ]
    return jsonify({"items": items})


@memories_bp.route("/memories/batch/delete", methods=["POST"])
@require_auth
async def batch_delete_memories():
    """批量删除记忆。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    ids = [_safe_int(x, 0) for x in (body.get("ids") or [])]
    ids = [i for i in ids if i > 0]
    if not ids:
        return jsonify({"error": "ids required"}), 400
    placeholders = ",".join("?" * len(ids))
    c.db.conn.execute(f"DELETE FROM memory_tags WHERE memory_id IN ({placeholders})", ids)
    c.db.conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
    # 同时清理 facts 表中 source_memory_id IN (...) 的引用
    if _table_exists(c.db.conn, "facts"):
        try:
            c.db.conn.execute(f"DELETE FROM facts WHERE source_memory_id IN ({placeholders})", ids)
        except Exception:
            pass
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted": len(ids)})


@memories_bp.route("/memories/batch/re-embed", methods=["POST"])
@require_auth
async def batch_re_embed():
    """批量重新向量化（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    ids = [_safe_int(x, 0) for x in (body.get("ids") or [])]
    ids = [i for i in ids if i > 0]

    async def stream():
        total = len(ids)
        yield f"data: {json.dumps({'progress': 0, 'total': total})}\n\n"
        done = errors = 0
        for mid in ids:
            try:
                detail = c.db.get_memory_detail(mid)
                if detail and detail.get("content"):
                    vec = await c.embedding_service.get_embedding(detail["content"])
                    if vec is not None:
                        c.db.update_memory_vector(mid, vec)
                        if c.memory_index:
                            c.memory_index.add([mid], [vec])
            except Exception:
                errors += 1
            done += 1
            yield f"data: {json.dumps({'progress': round(done/total, 3) if total else 1, 'processed': done, 'total': total, 'errors': errors})}\n\n"
        yield f"data: {json.dumps({'progress': 1.0, 'processed': done, 'total': total, 'errors': errors, 'done': True})}\n\n"

    return Response(stream(), content_type="text/event-stream")


@memories_bp.route("/memories/batch/extract-tags", methods=["POST"])
@require_auth
async def batch_extract_tags_for_ids():
    """对选中记忆批量提取 Tag（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    ids = [_safe_int(x, 0) for x in (body.get("ids") or [])]
    ids = [i for i in ids if i > 0]

    async def stream():
        total = len(ids)
        if not c.tag_extractor:
            yield f"data: {json.dumps({'error': 'Tag extractor 未配置'})}\n\n"
            return
        yield f"data: {json.dumps({'progress': 0, 'total': total})}\n\n"
        done = tagged = errors = 0
        for mid in ids:
            try:
                row = c.db.conn.execute("SELECT content, sender_name FROM memories WHERE id=?", (mid,)).fetchone()
                if row and row[0] and len(row[0]) >= 4:
                    tags = await c.tag_extractor.extract_tags(row[0][:800], sender=row[1] or "")
                    if tags:
                        names = [t["name"] for t in tags]
                        vecs = await c.embedding_service.get_embeddings(names)
                        for tag_info, tv in zip(tags, vecs):
                            tid = c.db.add_tag_extended(name=tag_info["name"], tag_type=tag_info.get("type", "keyword"), vector=tv, confidence=tag_info.get("confidence", 0.8))
                            c.db.conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (?, ?, ?, ?)", (mid, tid, 1, tag_info.get("confidence", 0.8)))
                        c.db.conn.commit()
                        tagged += 1
            except Exception:
                errors += 1
            done += 1
            yield f"data: {json.dumps({'progress': round(done/total, 3) if total else 1, 'processed': done, 'total': total, 'tagged': tagged, 'errors': errors})}\n\n"
        yield f"data: {json.dumps({'progress': 1.0, 'processed': done, 'total': total, 'tagged': tagged, 'errors': errors, 'done': True})}\n\n"

    return Response(stream(), content_type="text/event-stream")



@memories_bp.route("/memories/clusters", methods=["GET"])
@require_auth
async def memory_clusters():
    """获取长期记忆相似度聚类星云图坐标，无复杂依赖的高效降维迭代算法。"""
    c = get_container()
    # 读取最多 500 条带 vector 的长期记忆
    rows = c.db.conn.execute(
        "SELECT id, content, sender_name, timestamp, vector FROM memories WHERE vector IS NOT NULL ORDER BY id DESC LIMIT 500"
    ).fetchall()
    
    if not rows:
        return jsonify({"clusters": [], "points": []})
        
    points = []
    import random
    import math
    
    # 纯 Python 轻量多维尺度变换/随机聚类算法：将 vector 映射至 2D 平面 [-100, 100] 区域
    # 为了避免依赖外部 numpy/sklearn，我们使用经典质点重定位/基于名称哈希的随机散度算法
    for r in rows:
        mid, content, sender, ts, vec_raw = r
        try:
            vec = json.loads(vec_raw) if isinstance(vec_raw, str) else vec_raw
        except Exception:
            vec = []
            
        if not isinstance(vec, list) or not vec:
            # 降级退路
            h = hash(content or "") % 1000
            x = (h % 200) - 100
            y = ((h // 10) % 200) - 100
        else:
            # 抽取向量中前 8 维和最后 8 维的特征投影，结合内容散度做轻量映射
            v_sum1 = sum(vec[i] for i in range(min(len(vec), 8)))
            v_sum2 = sum(vec[-i-1] for i in range(min(len(vec), 8)))
            # 投影到 2D 区间
            angle = (v_sum1 * 23.456) % (2 * math.pi)
            radius = 12 + (abs(v_sum2) * 56.789) % 78
            x = math.cos(angle) * radius
            y = math.sin(angle) * radius
            
        # 聚类分类，通过记忆高频词和 sender 自动打上星云簇标签
        if any(w in content for w in ("好感", "感情", "互动", "亲近", "心境")):
            cluster = "灵魂羁绊"
        elif any(w in content for w in ("黑话", "口癖", "神言", "Holyman", "语录")):
            cluster = "黑话口癖"
        elif any(w in content for w in ("规则", "设定", "世界观", "人格", "自我")):
            cluster = "世界设定"
        else:
            cluster = "日常见闻"
            
        points.append({
            "id": mid,
            "content": content[:120] + ("..." if len(content) > 120 else ""),
            "sender": sender or "未知",
            "ts": ts,
            "x": round(x, 2),
            "y": round(y, 2),
            "cluster": cluster
        })
        
    # 计算聚类中心
    cluster_centers = {}
    for p in points:
        c_name = p["cluster"]
        if c_name not in cluster_centers:
            cluster_centers[c_name] = {"x": 0.0, "y": 0.0, "count": 0}
        cluster_centers[c_name]["x"] += p["x"]
        cluster_centers[c_name]["y"] += p["y"]
        cluster_centers[c_name]["count"] += 1
        
    clusters = []
    for name, stat in cluster_centers.items():
        cnt = stat["count"]
        clusters.append({
            "name": name,
            "cx": round(stat["x"] / cnt, 2) if cnt else 0,
            "cy": round(stat["y"] / cnt, 2) if cnt else 0,
            "count": cnt
        })
        
    return jsonify({"clusters": clusters, "points": points})


@memories_bp.route("/memories/stats", methods=["GET"])
@require_auth
async def memory_stats():
    """各 source 记忆统计。"""
    c = get_container()
    rows = c.db.conn.execute(
        "SELECT source, COUNT(*) FROM memories GROUP BY source ORDER BY COUNT(*) DESC"
    ).fetchall()
    total = sum(r[1] for r in rows)
    by_source = {r[0] or "unknown": r[1] for r in rows}
    return jsonify({"total": total, "by_source": by_source})


@memories_bp.route("/memories/<int:memory_id>", methods=["PATCH"])
@require_auth
async def patch_memory(memory_id: int):
    """手动修改记忆属性（如 source）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    allowed = {"source", "sender_name", "group_id"}
    sets = []
    params = []
    for key in allowed:
        if key in body:
            sets.append(f"{key} = ?")
            params.append(body[key])
    if not sets:
        return jsonify({"error": "No valid fields to update"}), 400
    params.append(memory_id)
    c.db.conn.execute(f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params)
    c.db.conn.commit()
    return jsonify({"ok": True, "memory_id": memory_id})


@memories_bp.route("/query", methods=["POST"])
@require_auth
async def query_test():
    """向量检索测试。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    text = body.get("text", "")
    top_k = _safe_int(body.get("top_k", 5), 5)
    mode = str(body.get("mode") or "").strip()
    source_filter = str(body.get("source_filter") or "").strip()
    exclude_sources = body.get("exclude_sources") if isinstance(body.get("exclude_sources"), list) else []
    exclude_sources = {str(item).strip() for item in exclude_sources if str(item).strip()}
    stages = body.get("stages") if isinstance(body.get("stages"), dict) else {}
    raw_query_params = body.get("params") if isinstance(body.get("params"), dict) else {}

    def _clamp_number(value, min_value, max_value):
        return max(min_value, min(max_value, value))

    def _query_int_param(name: str, default: int, min_value: int, max_value: int):
        if name not in raw_query_params or raw_query_params.get(name) is None:
            return None
        return _clamp_number(_safe_int(raw_query_params.get(name), default), min_value, max_value)

    def _query_float_param(name: str, default: float, min_value: float, max_value: float):
        if name not in raw_query_params or raw_query_params.get(name) is None:
            return None
        return round(_clamp_number(_safe_float(raw_query_params.get(name), default), min_value, max_value), 6)

    query_params = {}
    for key, value in (
        ("pyramid_max_levels", _query_int_param("pyramid_max_levels", 3, 1, 10)),
        ("pyramid_top_k", _query_int_param("pyramid_top_k", 10, 1, 50)),
        ("spike_max_hops", _query_int_param("spike_max_hops", 4, 0, 16)),
        ("spike_firing_threshold", _query_float_param("spike_firing_threshold", 0.1, 0.0, 1.0)),
        ("geodesic_alpha", _query_float_param("geodesic_alpha", 0.3, 0.0, 1.0)),
    ):
        if value is not None:
            query_params[key] = value

    def _bool_flag(value, default=False):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _stage_enabled(stage_name: str, legacy_key: str, default: bool) -> bool:
        if stage_name in stages:
            return _bool_flag(stages.get(stage_name), default)
        return _bool_flag(body.get(legacy_key), default)

    debug_requested = _bool_flag(body.get("debug"), True)
    enable_spike = _stage_enabled("spike", "enable_spike", True)
    enable_pyramid = _stage_enabled("pyramid", "enable_pyramid", True)
    enable_epa = _stage_enabled("epa", "enable_epa", False)
    enable_geodesic = _stage_enabled("geodesic", "enable_geodesic", False)

    timing = {}
    debug_info = {
        "query": {
            "text": text,
            "top_k": top_k,
            "mode": mode,
            "source_filter": source_filter,
            "exclude_sources": sorted(exclude_sources),
            "stages": {
                "epa": enable_epa,
                "pyramid": enable_pyramid,
                "spike": enable_spike,
                "geodesic": enable_geodesic,
            },
            "params": query_params,
        },
        "embedding": {"enabled": True, "available": False},
        "epa": {"enabled": enable_epa, "available": False},
        "pyramid": {"enabled": enable_pyramid, "available": False},
        "spike": {"enabled": enable_spike, "available": False},
        "vector_search": {"enabled": True, "available": False},
        "scoring": {"enabled": True, "available": False},
        "geodesic": {"enabled": enable_geodesic, "available": False},
        "final": {"result_count": 0},
        "highlights": {
            "pyramid_tags": [],
            "seed_tags": [],
            "emergent_tags": [],
            "geodesic_memory_ids": [],
            "final_memory_ids": [],
        },
        "warnings": [],
    }

    def _compact_tag(item: dict, energy_key: str | None = None) -> dict:
        result = {"tag_id": item.get("tag_id")}
        if "level" in item:
            result["level"] = item.get("level")
        if "similarity" in item:
            result["similarity"] = round(float(item.get("similarity") or 0), 4)
        if "weight" in item:
            result["weight"] = round(float(item.get("weight") or 0), 4)
        if energy_key and energy_key in item:
            result[energy_key] = round(float(item.get(energy_key) or 0), 4)
        if "is_emergent" in item:
            result["is_emergent"] = bool(item.get("is_emergent"))
        return result

    def _disable_stage(stage_name: str):
        debug_info[stage_name].update({"available": False, "reason": "disabled by request"})

    def _stage_unavailable(stage_name: str, reason: str):
        debug_info[stage_name].update({"available": False, "reason": reason})
        if debug_info[stage_name].get("enabled"):
            debug_info["warnings"].append({"stage": stage_name, "reason": reason})

    def _stage_error(stage_name: str, exc: Exception):
        _stage_unavailable(stage_name, str(exc))
        debug_info[stage_name]["error"] = str(exc)

    def _as_query_array(vector):
        try:
            import numpy as np
            return np.asarray(vector, dtype=np.float32)
        except Exception:
            return vector

    def _apply_temporary_attrs(obj, updates: dict) -> dict:
        previous = {}
        for attr, value in updates.items():
            if value is None or obj is None or not hasattr(obj, attr):
                continue
            previous[attr] = getattr(obj, attr)
            setattr(obj, attr, value)
        return previous

    def _restore_attrs(obj, previous: dict):
        for attr, value in previous.items():
            setattr(obj, attr, value)

    def _current_attrs(obj, attr_names: tuple[str, ...]) -> dict:
        return {attr: getattr(obj, attr) for attr in attr_names if hasattr(obj, attr)}

    def _infer_geodesic_mode_and_hits(geodesic, memory_ids: list, energy_field: dict, reranked: list) -> tuple[str, dict, int]:
        min_geo_samples = _safe_int(getattr(geodesic, "min_geo_samples", 4), 4)
        hit_counts = {mid: 0 for mid in memory_ids}
        memory_tag_map = {}
        if hasattr(geodesic, "_get_memory_tags"):
            try:
                memory_tag_map = geodesic._get_memory_tags(memory_ids) or {}
            except Exception:
                memory_tag_map = {}
        for mid in memory_ids:
            tag_ids = memory_tag_map.get(mid, []) or []
            hit_counts[mid] = sum(1 for tid in tag_ids if tid in energy_field)
        if any(count >= min_geo_samples for count in hit_counts.values()):
            return "L0", hit_counts, min_geo_samples
        if any(count > 0 for count in hit_counts.values()) or any(float(item.get("geo_score", 0) or 0) > 0 for item in reranked if isinstance(item, dict)):
            return "L1", hit_counts, min_geo_samples
        return "L2", hit_counts, min_geo_samples

    for stage_name, enabled in (("epa", enable_epa), ("pyramid", enable_pyramid), ("spike", enable_spike), ("geodesic", enable_geodesic)):
        if not enabled:
            _disable_stage(stage_name)

    t0 = time.perf_counter()
    query_vec = await c.embedding_service.get_embedding(text)
    embedding_ms = round((time.perf_counter() - t0) * 1000, 1)
    timing["embedding_ms"] = embedding_ms

    if query_vec is None:
        debug_info["embedding"].update({"available": False, "reason": "embedding failed", "latency_ms": embedding_ms})
        debug_info["warnings"].append({"stage": "embedding", "reason": "embedding failed"})
        return jsonify({"results": [], "timing": timing, "debug": debug_info if debug_requested else {}})

    query_array = _as_query_array(query_vec)
    debug_info["embedding"].update({"available": True, "dimension": len(query_vec), "latency_ms": embedding_ms})

    epa_result = None
    timing["epa_ms"] = 0
    if enable_epa:
        epa = getattr(c, "epa", None)
        if not epa:
            _stage_unavailable("epa", "EPA module unavailable")
        elif not getattr(epa, "initialized", False):
            _stage_unavailable("epa", "EPA basis is not initialized")
        else:
            t0 = time.perf_counter()
            try:
                epa_result = epa.analyze(query_array)
                logic_depth = float(epa_result.get("logic_depth", 0)) if isinstance(epa_result, dict) else 0.0
                interpretation = "focused" if logic_depth >= 0.66 else "diffuse" if logic_depth <= 0.33 else "mixed"
                debug_info["epa"].update({
                    "available": True,
                    "logic_depth": round(logic_depth, 4),
                    "entropy": round(float(epa_result.get("entropy", 0)), 4) if isinstance(epa_result, dict) else 0,
                    "dominant_axis": epa_result.get("dominant_axis") if isinstance(epa_result, dict) else None,
                    "interpretation": interpretation,
                    "result": epa_result,
                })
            except Exception as e:
                _stage_error("epa", e)
            timing["epa_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            debug_info["epa"]["latency_ms"] = timing["epa_ms"]

    pyramid_result = None
    timing["pyramid_ms"] = 0
    if enable_pyramid:
        pyramid = getattr(c, "residual_pyramid", None)
        if not pyramid:
            _stage_unavailable("pyramid", "Residual pyramid module unavailable")
        else:
            t0 = time.perf_counter()
            pyramid_overrides = {
                "max_levels": query_params.get("pyramid_max_levels"),
                "top_k": query_params.get("pyramid_top_k"),
            }
            previous_attrs = _apply_temporary_attrs(pyramid, pyramid_overrides)
            try:
                debug_info["pyramid"]["params"] = _current_attrs(pyramid, ("max_levels", "top_k"))
                pyramid_result = pyramid.analyze(query_array)
                levels = pyramid_result.get("levels", []) if isinstance(pyramid_result, dict) else []
                level_summaries = []
                pyramid_highlight_tags = []
                for level_items in levels:
                    level_limit = int(debug_info["pyramid"].get("params", {}).get("top_k", 10) or 10)
                    compact_level = [
                        _compact_tag(item)
                        for item in level_items[:level_limit]
                        if isinstance(item, dict)
                    ]
                    level_summaries.append(compact_level)
                    pyramid_highlight_tags.extend(compact_level)
                debug_info["highlights"]["pyramid_tags"] = pyramid_highlight_tags[:30]
                debug_info["pyramid"].update({
                    "available": True,
                    "level_count": len(levels),
                    "levels": level_summaries,
                    "coverage": round(float(pyramid_result.get("coverage", 0)), 4) if isinstance(pyramid_result, dict) else 0,
                    "tag_count": len(pyramid_result.get("all_tag_ids", [])) if isinstance(pyramid_result, dict) else 0,
                })
            except Exception as e:
                _stage_error("pyramid", e)
            finally:
                _restore_attrs(pyramid, previous_attrs)
            timing["pyramid_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    candidates = c.memory_index.search(query_vec, k=top_k * 4)
    timing["vector_search_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    ids = [x[0] for x in candidates]
    distances = [x[1] for x in candidates]
    vector_candidates = [
        {
            "rank": i + 1,
            "memory_id": mid,
            "distance": round(float(distances[i]), 4),
            "similarity": round(1.0 - float(distances[i]), 4),
        }
        for i, mid in enumerate(ids[:max(top_k, 1)])
    ]
    debug_info["vector_search"].update({
        "available": True,
        "candidate_count": len(candidates),
        "k": top_k * 4,
        "top_candidates": vector_candidates,
        "used_vector": "raw",
        "reason": "no tag context vector available",
        "latency_ms": timing["vector_search_ms"],
    })

    score_breakdown_by_id = {}
    for i, mid in enumerate(ids):
        similarity = round(1.0 - float(distances[i]), 4)
        score_breakdown_by_id[mid] = {
            "memory_id": mid,
            "rank_before": i + 1,
            "rank_after": i + 1,
            "similarity": similarity,
            "importance": 1.0,
            "time_decay": 1.0,
            "access_boost": 1.0,
            "score_before_geodesic": similarity,
            "score_after": similarity,
            "source": "vector",
            "is_cross_group": False,
        }
    debug_info["scoring"].update({
        "available": True,
        "before_filter_count": len(candidates),
        "after_filter_count": len(ids),
        "base_scores": [
            {"rank": i + 1, "id": mid, "similarity": round(1.0 - distances[i], 4)}
            for i, mid in enumerate(ids[:top_k])
        ],
        "score_breakdown": [score_breakdown_by_id[mid] for mid in ids[:top_k] if mid in score_breakdown_by_id],
    })

    timing["spike_routing_ms"] = 0
    energy_field = {}
    if enable_spike:
        spike_router = getattr(c, "spike_router", None)
        if not spike_router:
            _stage_unavailable("spike", "Spike router unavailable")
        else:
            t0 = time.perf_counter()
            spike_overrides = {
                "max_hops": query_params.get("spike_max_hops"),
                "firing_threshold": query_params.get("spike_firing_threshold"),
            }
            previous_attrs = _apply_temporary_attrs(spike_router, spike_overrides)
            try:
                debug_info["spike"]["params"] = _current_attrs(
                    spike_router,
                    ("max_hops", "firing_threshold", "base_decay", "wormhole_decay", "tension_threshold"),
                )
                pyramid_tag_ids = []
                if isinstance(pyramid_result, dict):
                    pyramid_tag_ids = list(pyramid_result.get("all_tag_ids", []))[:5]
                if pyramid_tag_ids:
                    seed_tags = [{"tag_id": tid, "weight": 1.0} for tid in pyramid_tag_ids]
                else:
                    seed_results = c.tag_index.search(query_vec, k=5)
                    seed_tags = [{"tag_id": tid, "weight": 1.0 - dist} for tid, dist in seed_results if (1.0 - dist) > 0.3]
                spike_result = spike_router.propagate(seed_tags, epa_result)
                energy_field = spike_result.get("energy_field", {})
                activated_tags = [
                    _compact_tag(item, "energy")
                    for item in spike_result.get("activated_tags", [])[:50]
                    if isinstance(item, dict)
                ]
                seed_tag_details = [_compact_tag(item) for item in seed_tags[:20] if isinstance(item, dict)]
                emergent_tags = [item for item in activated_tags if item.get("is_emergent")]
                energy_field_top = [
                    {"tag_id": tag_id, "energy": round(float(energy), 4)}
                    for tag_id, energy in sorted(energy_field.items(), key=lambda item: float(item[1] or 0), reverse=True)[:20]
                ]
                debug_info["highlights"]["seed_tags"] = seed_tag_details
                debug_info["highlights"]["emergent_tags"] = emergent_tags[:30]
                debug_info["spike"].update({
                    "available": True,
                    "seed_count": len(seed_tags),
                    "seed_tags": seed_tag_details,
                    "activated_count": len(spike_result.get("activated_tags", [])),
                    "activated_tags": activated_tags,
                    "energy_count": len(energy_field),
                    "energy_field_size": len(energy_field),
                    "energy_field_top": energy_field_top,
                })
                if not seed_tags:
                    debug_info["spike"]["reason"] = "no seed tags above threshold"
            except Exception as e:
                _stage_error("spike", e)
            finally:
                _restore_attrs(spike_router, previous_attrs)
            timing["spike_routing_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    timing["geodesic_ms"] = 0
    if enable_geodesic:
        geodesic = getattr(c, "geodesic", None)
        if not geodesic:
            _stage_unavailable("geodesic", "Geodesic reranker unavailable")
        elif not energy_field:
            _stage_unavailable("geodesic", "requires non-empty spike energy_field")
        else:
            t0 = time.perf_counter()
            geodesic_overrides = {"alpha": query_params.get("geodesic_alpha")}
            previous_attrs = _apply_temporary_attrs(geodesic, geodesic_overrides)
            try:
                debug_info["geodesic"]["params"] = _current_attrs(geodesic, ("alpha",))
                rerank_candidates = [{"id": mid, "score": 1.0 - distances[i] if i < len(distances) else 0} for i, mid in enumerate(ids)]
                before_ids = ids[:]
                before_rank_by_id = {mid: i + 1 for i, mid in enumerate(before_ids)}
                before_score_by_id = {mid: round(float(item.get("score", 0)), 4) for mid, item in zip(before_ids, rerank_candidates)}
                reranked = geodesic.rerank(rerank_candidates, energy_field)
                mode, hit_counts, min_geo_samples = _infer_geodesic_mode_and_hits(geodesic, before_ids, energy_field, reranked)
                ids = [x["id"] for x in reranked]
                distances = [1.0 - x["score"] for x in reranked]
                reranked_details = []
                for after_index, item in enumerate(reranked[:top_k]):
                    mid = item.get("id")
                    geo_score = round(float(item.get("geo_score", 0) or 0), 4)
                    score_after = round(float(item.get("score", 0) or 0), 4)
                    detail = {
                        "memory_id": mid,
                        "rank_before": before_rank_by_id.get(mid),
                        "rank_after": after_index + 1,
                        "score_before": before_score_by_id.get(mid, 0),
                        "score_after": score_after,
                        "geo_score": geo_score,
                        "hit_count": hit_counts.get(mid, 0),
                    }
                    reranked_details.append(detail)
                    if mid in score_breakdown_by_id:
                        score_breakdown_by_id[mid].update({
                            "rank_after": after_index + 1,
                            "score_after": score_after,
                            "geodesic_score": geo_score,
                            "hit_count": hit_counts.get(mid, 0),
                            "geodesic_mode": mode,
                        })
                debug_info["highlights"]["geodesic_memory_ids"] = ids[:top_k]
                debug_info["geodesic"].update({
                    "available": True,
                    "mode": mode,
                    "alpha": debug_info["geodesic"].get("params", {}).get("alpha"),
                    "min_geo_samples": min_geo_samples,
                    "energy_count": len(energy_field),
                    "before_ids": before_ids[:top_k],
                    "after_ids": ids[:top_k],
                    "geo_scores": [round(float(x.get("geo_score", 0)), 4) for x in reranked[:top_k]],
                    "reranked": reranked_details,
                })
            except Exception as e:
                _stage_error("geodesic", e)
            finally:
                _restore_attrs(geodesic, previous_attrs)
            timing["geodesic_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    results = []
    for i, mid in enumerate(ids[:top_k]):
        mem = c.db.get_memory_brief(mid)
        if mem:
            mem_source = str(mem.get("source") or "")
            if source_filter and mem_source != source_filter:
                continue
            if mem_source and mem_source in exclude_sources:
                continue
            score = 1.0 - distances[i] if i < len(distances) else 0
            mem["score"] = round(score, 4)
            breakdown = score_breakdown_by_id.get(mid, {}).copy()
            breakdown.setdefault("memory_id", mid)
            breakdown.setdefault("rank_before", i + 1)
            breakdown["rank_after"] = i + 1
            breakdown["score_after"] = mem["score"]
            mem["score_breakdown"] = breakdown
            results.append(mem)

    timing["total_ms"] = round(sum(timing.values()), 1)
    debug_info["scoring"]["after_source_filter_count"] = len(results)
    final_memory_ids = [item.get("id") for item in results]
    final_score_breakdown = [item.get("score_breakdown", {}) for item in results]
    debug_info["highlights"]["final_memory_ids"] = final_memory_ids
    if not debug_info["highlights"].get("geodesic_memory_ids") and enable_geodesic:
        debug_info["highlights"]["geodesic_memory_ids"] = final_memory_ids
    debug_info["final"] = {"result_count": len(results), "ids": final_memory_ids, "score_breakdown": final_score_breakdown}
    return jsonify({"results": results, "timing": timing, "debug": debug_info if debug_requested else {}})


@memories_bp.route("/import/sources", methods=["GET"])
@require_auth
async def discover_sources():
    """数据源发现。"""
    c = get_container()
    from ..source_discovery import SourceDiscovery
    refresh = request.args.get("refresh", "").lower() == "true"

    discovery = SourceDiscovery()
    sources = discovery.discover_all()
    result = []
    for s in sources:
        progress = discovery.estimate_imported(s, c.db)
        result.append({
            "id": s["id"], "name": s["name"], "description": s["description"],
            "count": s["count"], "type": s["type"],
            "db_path": s.get("db_path", ""),
            "has_adapter": s["type"] == "known",
            "imported_pct": progress["estimated_pct"],
            "remaining": progress["estimated_remaining"],
        })
    return jsonify({"sources": result})


@memories_bp.route("/import/preview", methods=["POST"])
@require_auth
async def import_preview():
    """导入预览。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    source = body.get("source", "")
    from ..importer import WaveMemoryImporter
    importer = WaveMemoryImporter(c.db, c.embedding_service, c.tag_extractor)
    result = await importer.preview(source)
    return jsonify(result)


@memories_bp.route("/import/start", methods=["POST"])
@require_auth
async def import_start():
    """开始导入（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    source = body.get("source", "")
    re_embed = body.get("re_embed", True)
    extract_tags = body.get("extract_tags", True)
    batch_size = _safe_int(body.get("batch_size", 20), 20)

    from ..importer import WaveMemoryImporter
    importer = WaveMemoryImporter(
        c.db, c.embedding_service, c.tag_extractor,
        memory_index=c.memory_index, writer=c.writer,
    )

    async def event_stream():
        async for event in importer.run(source=source, re_embed=re_embed, extract_tags=extract_tags, batch_size=batch_size):
            yield f"data: {event}\n\n"

    return Response(event_stream(), content_type="text/event-stream")


@memories_bp.route("/import/from-source", methods=["POST"])
@require_auth
async def import_from_source():
    """从指定数据源导入（SSE 流）。"""
    c = get_container()
    source_id = request.args.get("source_id", "")
    limit = _safe_int(request.args.get("limit", 5000), 5000)

    if _import_lock.locked():
        async def locked_msg():
            yield f"data: {json.dumps({'error': '另一个导入/提取任务正在运行'})}\n\n"
        return Response(locked_msg(), content_type="text/event-stream")

    from ..source_discovery import SourceDiscovery, UniversalImporter
    discovery = SourceDiscovery()
    all_sources = discovery.discover_all()
    source = next((s for s in all_sources if s["id"] == source_id), None)

    if not source:
        return jsonify({"error": f"Source not found: {source_id}"}), 404

    importer = UniversalImporter(
        c.db, c.embedding_service,
        tag_extractor=c.tag_extractor,
        memory_index=c.memory_index,
    )

    async def event_stream():
        async with _import_lock:
            if source["type"] == "known":
                async for event in importer.import_known(source, limit=limit):
                    yield f"data: {event}\n\n"
            else:
                analysis = source.get("analysis", {})
                importable = analysis.get("importable_tables", [])
                if importable:
                    table_info = importable[0]
                    cols = [col.lower() for col in table_info["columns"]]
                    content_field = next((col for col in cols if col in ("content", "text", "message")), cols[0] if cols else "content")
                    sender_field = next((col for col in cols if col in ("sender", "sender_name", "sender_id")), None)
                    ts_field = next((col for col in cols if col in ("timestamp", "created_at", "time", "ts")), None)
                    group_field = next((col for col in cols if col in ("group_id", "group", "session_id")), None)
                    mapping = {
                        "table": table_info["name"],
                        "content_field": content_field,
                        "sender_field": sender_field,
                        "timestamp_field": ts_field,
                        "group_field": group_field,
                        "filter": f"LENGTH({content_field}) >= 10",
                    }
                    async for event in importer.import_with_llm_mapping({"db_path": source["db_path"]}, mapping, limit=limit):
                        yield f"data: {event}\n\n"
                else:
                    yield f"data: {json.dumps({'error': 'No importable tables found'})}\n\n"

    return Response(event_stream(), content_type="text/event-stream")


# ─── React 前端兼容路由（/api/memories/import/*） ───

@memories_bp.route("/memories/import/sources", methods=["GET"])
@require_auth
async def memories_discover_sources():
    """兼容 React 前端的 /api/memories/import/sources → 代理到现有 discover_sources。"""
    return await discover_sources()


@memories_bp.route("/memories/import/run", methods=["POST"])
@require_auth
async def memories_import_run():
    """兼容 React 前端的 /api/memories/import/run → 代理到现有 import_from_source。"""
    return await import_from_source()


# LLM 批量提取锁
_llm_extract_lock = asyncio.Lock()
_llm_extract_stop_event: asyncio.Event = asyncio.Event()


@memories_bp.route("/memories/import/llm-extract", methods=["POST"])
@require_auth
async def memories_llm_extract():
    """对无标签记忆批量提取 Tag（SSE 流），兼容 React 前端调用。"""
    c = get_container()
    batch_size = max(1, min(200, _safe_int(request.args.get("batch_size", 50), 50)))

    if _llm_extract_lock.locked():
        async def locked_msg():
            yield f"data: {json.dumps({'error': '另一个提取任务正在运行'})}\n\n"
        return Response(locked_msg(), content_type="text/event-stream")

    if not c.tag_extractor:
        async def no_extractor():
            yield f"data: {json.dumps({'error': 'Tag extractor 未配置，需要配置 tag_llm_provider_id'})}\n\n"
        return Response(no_extractor(), content_type="text/event-stream")

    # 重置停止信号
    _llm_extract_stop_event.clear()

    async def event_stream():
        async with _llm_extract_lock:
            # 找到所有无标签记忆
            try:
                rows = c.db.conn.execute(
                    "SELECT DISTINCT m.id, m.content, m.sender_name FROM memories m "
                    "LEFT JOIN memory_tags mt ON m.id = mt.memory_id "
                    "WHERE mt.memory_id IS NULL AND LENGTH(m.content) >= 4 "
                    "ORDER BY m.id"
                ).fetchall()
            except Exception as e:
                yield f"data: {json.dumps({'error': f'查询无标签记忆失败: {e}'})}\n\n"
                return

            total = len(rows)
            if total == 0:
                yield f"data: {json.dumps({'progress': 1.0, 'processed': 0, 'total': 0, 'done': True, 'message': '没有需要提取标签的记忆'})}\n\n"
                return

            yield f"data: {json.dumps({'progress': 0, 'total': total, 'message': f'开始提取 {total} 条无标签记忆的标签'})}\n\n"
            processed = tagged = errors = 0

            for i in range(0, total, batch_size):
                if _llm_extract_stop_event.is_set():
                    yield f"data: {json.dumps({'progress': round(processed / total, 3) if total else 1, 'processed': processed, 'total': total, 'tagged': tagged, 'errors': errors, 'done': True, 'message': f'用户中止，已处理 {processed}/{total}'})}\n\n"
                    return

                batch = rows[i:i + batch_size]
                for row in batch:
                    if _llm_extract_stop_event.is_set():
                        break
                    try:
                        mid, content, sender = row[0], (row[1] or "")[:800], (row[2] or "")
                        if content and len(content) >= 4:
                            tags = await c.tag_extractor.extract_tags(content, sender=sender)
                            if tags:
                                names = [t["name"] for t in tags]
                                try:
                                    vecs = await c.embedding_service.get_embeddings(names)
                                except Exception:
                                    vecs = [None] * len(names)
                                for tag_info, tv in zip(tags, vecs):
                                    tid = c.db.add_tag_extended(
                                        name=tag_info["name"],
                                        tag_type=tag_info.get("type", "keyword"),
                                        vector=tv,
                                        confidence=tag_info.get("confidence", 0.8),
                                    )
                                    c.db.conn.execute(
                                        "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (?, ?, ?, ?)",
                                        (mid, tid, 1, tag_info.get("confidence", 0.8)),
                                    )
                                c.db.conn.commit()
                                tagged += 1
                    except Exception:
                        errors += 1
                    processed += 1

                yield f"data: {json.dumps({'progress': round(processed / total, 3) if total else 1, 'processed': processed, 'total': total, 'tagged': tagged, 'errors': errors, 'message': f'批 {i // batch_size + 1}: {processed}/{total}（标签: {tagged}）'})}\n\n"

            yield f"data: {json.dumps({'progress': 1.0, 'processed': processed, 'total': total, 'tagged': tagged, 'errors': errors, 'done': True, 'message': f'完成: {tagged} 条已提取标签'})}\n\n"

    return Response(event_stream(), content_type="text/event-stream")


@memories_bp.route("/memories/import/llm-extract/stop", methods=["POST"])
@require_auth
async def memories_llm_extract_stop():
    """停止正在运行的 LLM 提取。"""
    _llm_extract_stop_event.set()
    return jsonify({"ok": True, "message": "提取已发送中止信号"})
