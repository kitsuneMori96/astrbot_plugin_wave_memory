"""Tags Blueprint — Tag CRUD、审计、批量操作"""

from __future__ import annotations

import asyncio
import json
import math

from quart import Blueprint, jsonify, request, Response

from ..container import get_container
from ..middleware.auth import require_auth

tags_bp = Blueprint("tags", __name__, url_prefix="/api/tags")


@tags_bp.route("/", methods=["GET"])
@require_auth
async def list_tags():
    """分页查看 Tag 列表。"""
    c = get_container()
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    tag_type = request.args.get("type")
    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "frequency")

    sql = "SELECT id, name, tag_type, frequency, confidence FROM tags WHERE 1=1"
    params = []
    if tag_type:
        sql += " AND tag_type = ?"
        params.append(tag_type)
    if search:
        sql += " AND name LIKE ?"
        params.append(f"%{search}%")

    order = "frequency DESC" if sort == "frequency" else "id DESC"
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    total = c.db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    items = [{"id": r[0], "name": r[1], "type": r[2], "frequency": r[3], "confidence": r[4]} for r in rows]
    return jsonify({"items": items, "total": total})


@tags_bp.route("/retype", methods=["POST"])
@require_auth
async def retype_tag():
    """修改 Tag 类型。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    tag_id = body.get("tag_id")
    new_type = body.get("new_type")
    if not tag_id or not new_type:
        return jsonify({"error": "tag_id and new_type required"}), 400
    valid_types = {"keyword", "topic", "event", "entity", "fact", "emotion", "person", "location", "time"}
    if new_type not in valid_types:
        return jsonify({"error": f"Invalid type. Valid: {sorted(valid_types)}"}), 400
    c.db.conn.execute("UPDATE tags SET tag_type = ? WHERE id = ?", (new_type, tag_id))
    c.db.conn.commit()
    return jsonify({"tag_id": tag_id, "new_type": new_type})


@tags_bp.route("/rename", methods=["POST"])
@require_auth
async def rename_tag():
    """重命名 Tag。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    tag_id = body.get("tag_id")
    new_name = (body.get("new_name") or "").strip()
    if not tag_id or not new_name:
        return jsonify({"error": "tag_id and new_name required"}), 400

    existing = c.db.conn.execute("SELECT id FROM tags WHERE name = ? AND id != ?", (new_name, tag_id)).fetchone()
    if existing:
        return jsonify({"error": f"Tag '{new_name}' already exists (id={existing[0]})"}), 409

    old_row = c.db.conn.execute("SELECT name, aliases FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if not old_row:
        return jsonify({"error": f"Tag {tag_id} not found"}), 404

    old_name = old_row[0]
    old_aliases = (old_row[1] or "").split(",") if old_row[1] else []
    if old_name not in old_aliases:
        old_aliases.append(old_name)
    old_aliases = [a for a in old_aliases if a and a != new_name]

    c.db.conn.execute("UPDATE tags SET name = ?, aliases = ? WHERE id = ?", (new_name, ",".join(old_aliases), tag_id))
    c.db.conn.commit()
    return jsonify({"tag_id": tag_id, "old_name": old_name, "new_name": new_name})


@tags_bp.route("/batch-delete", methods=["POST"])
@require_auth
async def batch_delete_tags():
    """批量删除 Tag。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    tag_ids = body.get("tag_ids", [])
    if not tag_ids:
        return jsonify({"error": "tag_ids required"}), 400

    placeholders = ",".join("?" * len(tag_ids))
    c.db.conn.execute(f"DELETE FROM memory_tags WHERE tag_id IN ({placeholders})", tag_ids)
    c.db.conn.execute(f"DELETE FROM tag_relations WHERE source_tag_id IN ({placeholders}) OR target_tag_id IN ({placeholders})", tag_ids + tag_ids)
    c.db.conn.execute(f"DELETE FROM tags WHERE id IN ({placeholders})", tag_ids)
    c.db.conn.commit()
    return jsonify({"deleted": len(tag_ids)})


@tags_bp.route("/quality", methods=["GET"])
@require_auth
async def tag_quality():
    """Tag 质量概览：总数 + 覆盖率（有 tag 的记忆占比）。"""
    c = get_container()
    total_tags = c.db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    total_mem = c.db.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    tagged_mem = c.db.conn.execute(
        "SELECT COUNT(DISTINCT memory_id) FROM memory_tags"
    ).fetchone()[0]
    coverage = (tagged_mem / total_mem) if total_mem else 0.0
    return jsonify({
        "total_tags": total_tags,
        "total_memories": total_mem,
        "tagged_memories": tagged_mem,
        "coverage": round(coverage, 4),
    })


@tags_bp.route("/audit/trigger", methods=["GET"])
@require_auth
async def trigger_audit():
    """触发 Tag 审计（SSE 流）。strategy: mixed/lowconf/orphan/duplicate；total_count 上限。"""
    c = get_container()
    strategy = request.args.get("strategy", "mixed")
    total_count = int(request.args.get("total_count", 500))
    total_count = max(10, min(2000, total_count))

    # TagAuditor 审计需 LLM：从 container 取 context + provider_id
    context = None
    provider_id = ""
    try:
        context = c.embedding_service.context if c.embedding_service else None
        provider_id = (c.plugin_config or {}).get("tag_llm_provider_id", "")
    except Exception:
        pass

    async def stream():
        from ...services.tag_auditor import TagAuditor
        yield f"data: {json.dumps({'progress': 0, 'message': '正在准备审计...'}, ensure_ascii=False)}\n\n"
        if not provider_id or not context:
            yield f"data: {json.dumps({'error': '未配置 Tag LLM Provider，无法审计', 'done': True}, ensure_ascii=False)}\n\n"
            return
        # 校验 provider 是否真实可用（避免配置失效时静默跑空）
        try:
            prov = context.get_provider_by_id(provider_id) if hasattr(context, "get_provider_by_id") else None
            if prov is None:
                avail = []
                try:
                    avail = [p.meta().id for p in context.get_all_providers()][:8]
                except Exception:
                    pass
                yield f"data: {json.dumps({'error': f'Provider \"{provider_id}\" 不存在，请在配置中重新选择。可用: {avail}', 'done': True}, ensure_ascii=False)}\n\n"
                return
        except Exception:
            pass
        if _import_lock.locked():
            yield f"data: {json.dumps({'error': '另一个任务正在运行', 'done': True}, ensure_ascii=False)}\n\n"
            return
        auditor = TagAuditor(db=c.db, context=context, provider_id=provider_id)
        async with _import_lock:
            try:
                async for event in auditor.run_audit(strategy=strategy, total_count=total_count):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"
                return
        yield f"data: {json.dumps({'progress': 100, 'done': True, 'message': '审计完成'}, ensure_ascii=False)}\n\n"

    return Response(stream(), content_type="text/event-stream")


@tags_bp.route("/audit/suggestions", methods=["GET"])
@require_auth
async def get_audit_suggestions():
    """获取审计建议列表。"""
    c = get_container()
    from ...services.tag_auditor import TagAuditor
    status = request.args.get("status", "pending")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    action = request.args.get("action")

    auditor = TagAuditor(db=c.db)
    suggestions = auditor.get_suggestions(status=status, limit=limit, offset=offset, action=action)
    counts = auditor.get_suggestion_counts()
    return jsonify({"suggestions": suggestions, "counts": counts})


@tags_bp.route("/audit/resolve", methods=["POST"])
@require_auth
async def resolve_audit_suggestion():
    """批准或拒绝审计建议。"""
    c = get_container()
    from ...services.tag_auditor import TagAuditor
    body = await request.get_json(silent=True) or {}
    suggestion_id = body.get("suggestion_id")
    decision = body.get("decision")
    if not suggestion_id or decision not in ("approve", "reject"):
        return jsonify({"error": "suggestion_id and decision (approve/reject) required"}), 400
    auditor = TagAuditor(db=c.db)
    result = auditor.resolve_suggestion(suggestion_id, decision)
    return jsonify(result)


@tags_bp.route("/audit/resolve-batch", methods=["POST"])
@require_auth
async def resolve_audit_batch():
    """批量处理审计建议。"""
    c = get_container()
    from ...services.tag_auditor import TagAuditor
    body = await request.get_json(silent=True) or {}
    items = body.get("items", [])
    if not items:
        ids = body.get("suggestion_ids", [])
        decision = body.get("decision")
        if ids and decision:
            items = [{"id": sid, "decision": decision} for sid in ids]
    if not items:
        return jsonify({"error": "items or suggestion_ids+decision required"}), 400

    auditor = TagAuditor(db=c.db)
    results = []
    for item in items:
        sid = item.get("id")
        dec = item.get("decision")
        if sid and dec in ("approve", "reject"):
            results.append(auditor.resolve_suggestion(sid, dec))
    return jsonify({"processed": len(results), "results": results})


@tags_bp.route("/batch-extract", methods=["POST"])
@require_auth
async def batch_extract_tags():
    """批量 Tag 提取（SSE 流）。"""
    c = get_container()
    batch_size = int(request.args.get("batch_size", 50))
    batch_size = max(1, min(500, batch_size))

    if _import_lock.locked():
        async def locked_msg():
            yield f"data: {json.dumps({'error': '另一个导入/提取任务正在运行'})}\n\n"
        return Response(locked_msg(), content_type="text/event-stream")

    async def run_batch():
        async with _import_lock:
            if not c.tag_extractor:
                yield f"data: {json.dumps({'error': 'Tag extractor not configured'})}\n\n"
                return
            rows = c.db.conn.execute(
                """SELECT m.id, m.content, m.sender_name FROM memories m
                   WHERE m.id NOT IN (SELECT DISTINCT memory_id FROM memory_tags)
                   AND LENGTH(m.content) >= 10
                   ORDER BY m.id DESC LIMIT 5000"""
            ).fetchall()
            total = len(rows)
            if total == 0:
                yield f"data: {json.dumps({'progress': 1.0, 'message': 'All memories already have tags'})}\n\n"
                return
            yield f"data: {json.dumps({'progress': 0, 'total': total})}\n\n"
            processed = tagged = errors = 0
            for i in range(0, total, batch_size):
                batch = rows[i:i + batch_size]
                for mem_id, content, sender_name in batch:
                    try:
                        tags = await c.tag_extractor.extract_tags(content[:800], sender=sender_name or "")
                        if tags:
                            tag_names = [t["name"] for t in tags]
                            tag_vecs = await c.embedding_service.get_embeddings(tag_names)
                            tag_ids = []
                            for tag_info, tag_vec in zip(tags, tag_vecs):
                                tid = c.db.add_tag_extended(name=tag_info["name"], tag_type=tag_info.get("type", "keyword"), vector=tag_vec, confidence=tag_info.get("confidence", 0.8))
                                tag_ids.append(tid)
                            for pos, (tid, tag_info) in enumerate(zip(tag_ids, tags), 1):
                                c.db.conn.execute("INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (?, ?, ?, ?)", (mem_id, tid, pos, tag_info.get("confidence", 0.8)))
                            c.db.conn.commit()
                            tagged += 1
                    except Exception:
                        errors += 1
                    processed += 1
                yield f"data: {json.dumps({'progress': round(processed/total, 3), 'processed': processed, 'total': total, 'tagged': tagged, 'errors': errors})}\n\n"
                await asyncio.sleep(0.1)
            yield f"data: {json.dumps({'progress': 1.0, 'processed': total, 'tagged': tagged, 'errors': errors})}\n\n"

    return Response(run_batch(), content_type="text/event-stream")


_import_lock = asyncio.Lock()
