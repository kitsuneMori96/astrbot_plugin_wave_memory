"""Memories Blueprint — 记忆查询、导入、统计"""

from __future__ import annotations

import asyncio
import json
import time

from quart import Blueprint, jsonify, request, Response

from ..container import get_container
from ..middleware.auth import require_auth

memories_bp = Blueprint("memories", __name__, url_prefix="/api")

_import_lock = asyncio.Lock()


@memories_bp.route("/memories", methods=["GET"])
@require_auth
async def list_memories():
    """分页查看记忆列表。"""
    c = get_container()
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    source = request.args.get("source")
    sender_id = request.args.get("sender_id")
    group_id = request.args.get("group_id")
    limit = max(1, min(200, limit))

    sql = "SELECT id, content, sender_id, sender_name, group_id, source, timestamp FROM memories WHERE 1=1"
    params = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    if sender_id:
        sql += " AND sender_id = ?"
        params.append(sender_id)
    if group_id:
        sql += " AND group_id = ?"
        params.append(group_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    total = c.db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    items = [
        {"id": r[0], "content": r[1], "sender_id": r[2], "sender_name": r[3],
         "group_id": r[4], "source": r[5], "timestamp": r[6]}
        for r in rows
    ]
    return jsonify({"items": items, "total": total, "limit": limit, "offset": offset})


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
    top_k = int(body.get("top_k", 5))
    enable_spike = body.get("enable_spike", True)
    enable_pyramid = body.get("enable_pyramid", True)
    enable_epa = body.get("enable_epa", False)
    enable_geodesic = body.get("enable_geodesic", False)

    timing = {}
    debug_info = {}

    t0 = time.perf_counter()
    query_vec = await c.embedding_service.get_embedding(text)
    timing["embedding_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    if query_vec is None:
        return jsonify({"results": [], "timing": timing, "debug": {"error": "embedding failed"}})

    debug_info["query_vector_dim"] = len(query_vec)

    t0 = time.perf_counter()
    candidates = c.memory_index.search(query_vec, k=top_k * 4)
    timing["vector_search_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    debug_info["candidates_before_rerank"] = len(candidates)

    ids = [x[0] for x in candidates]
    distances = [x[1] for x in candidates]

    # Spike Routing
    timing["spike_routing_ms"] = 0
    energy_field = {}
    if enable_spike and c.spike_router:
        t0 = time.perf_counter()
        try:
            seed_results = c.tag_index.search(query_vec, k=5)
            seed_tags = [{"tag_id": tid, "weight": 1.0 - dist} for tid, dist in seed_results if (1.0 - dist) > 0.3]
            if seed_tags:
                spike_result = c.spike_router.propagate(seed_tags)
                energy_field = spike_result.get("energy_field", {})
                debug_info["spike_seeds"] = len(seed_tags)
                debug_info["spike_activated"] = len(spike_result.get("activated_tags", []))
        except Exception as e:
            debug_info["spike_error"] = str(e)
        timing["spike_routing_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    # Geodesic Rerank
    timing["geodesic_ms"] = 0
    if enable_geodesic and c.geodesic and energy_field:
        t0 = time.perf_counter()
        try:
            rerank_candidates = [{"id": mid, "score": 1.0 - distances[i] if i < len(distances) else 0} for i, mid in enumerate(ids)]
            reranked = c.geodesic.rerank(rerank_candidates, energy_field)
            ids = [x["id"] for x in reranked]
            distances = [1.0 - x["score"] for x in reranked]
        except Exception as e:
            debug_info["geodesic_error"] = str(e)
        timing["geodesic_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    results = []
    for i, mid in enumerate(ids[:top_k]):
        mem = c.db.get_memory_brief(mid)
        if mem:
            score = 1.0 - distances[i] if i < len(distances) else 0
            mem["score"] = round(score, 4)
            results.append(mem)

    timing["total_ms"] = round(sum(timing.values()), 1)
    return jsonify({"results": results, "timing": timing, "debug": debug_info})


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
    batch_size = int(body.get("batch_size", 20))

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
    limit = int(request.args.get("limit", 5000))

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
