"""Tags Blueprint — Tag CRUD、审计、批量操作"""

from __future__ import annotations

import asyncio
import json
import math
import time

from quart import Blueprint, jsonify, request, Response

from ..container import get_container
from ..middleware.auth import require_auth
from ..tag_execution import normalize_tag_execution_options, tag_memory_batch

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


def build_tag_quality_payload(conn) -> dict:
    """构造 Tag 质量概览，所有 memory 口径都必须以真实 memories 表为准。

    历史清理/删除可能留下 memory_tags 孤儿行；这些行不能算作“已标记记忆”，
    否则前端待处理数量会被严重压低。
    """
    total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    total_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    tagged_mem = conn.execute(
        """SELECT COUNT(DISTINCT m.id)
           FROM memories m
           JOIN memory_tags mt ON mt.memory_id = m.id"""
    ).fetchone()[0]
    untagged_mem = conn.execute(
        """SELECT COUNT(*) FROM memories m
           WHERE NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = m.id)"""
    ).fetchone()[0]
    extractable_untagged = conn.execute(
        """SELECT COUNT(*) FROM memories m
           WHERE NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = m.id)
           AND LENGTH(COALESCE(m.content, '')) >= 10"""
    ).fetchone()[0]
    skipped_short = conn.execute(
        """SELECT COUNT(*) FROM memories m
           WHERE NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = m.id)
           AND LENGTH(COALESCE(m.content, '')) < 10"""
    ).fetchone()[0]
    orphan_refs = conn.execute(
        """SELECT COUNT(DISTINCT mt.memory_id)
           FROM memory_tags mt
           LEFT JOIN memories m ON m.id = mt.memory_id
           WHERE m.id IS NULL"""
    ).fetchone()[0]
    coverage = (tagged_mem / total_mem) if total_mem else 0.0
    return {
        "total_tags": total_tags,
        "total_memories": total_mem,
        "tagged_memories": tagged_mem,
        "untagged_memories": untagged_mem,
        "extractable_untagged_memories": extractable_untagged,
        "skipped_short_untagged_memories": skipped_short,
        "orphan_memory_tag_refs": orphan_refs,
        "coverage": round(coverage, 4),
    }


@tags_bp.route("/quality", methods=["GET"])
@require_auth
async def tag_quality():
    """Tag 质量概览：总数 + 覆盖率（有 tag 的真实记忆占比）。"""
    c = get_container()
    return jsonify(build_tag_quality_payload(c.db.conn))


@tags_bp.route("/audit/trigger", methods=["GET", "POST"])
@require_auth
async def trigger_audit():
    """Queue Tag audit as a durable job and return its pollable job id."""
    c = get_container()
    strategy = request.args.get("strategy", "mixed")
    if strategy not in {"mixed", "low_quality", "high_freq"}:
        return jsonify({"ok": False, "error": "invalid_audit_strategy"}), 400
    total_count = max(10, min(2000, int(request.args.get("total_count", 500))))
    provider_id = str((c.plugin_config or {}).get("tag_llm_provider_id", ""))
    if not provider_id:
        return jsonify({"ok": False, "error": "tag_audit_provider_not_configured"}), 409
    jobs = getattr(c, "durable_jobs", None)
    if jobs is None:
        return jsonify({"ok": False, "error": "durable_jobs_unavailable"}), 503

    schedule_slot = str(
        request.args.get("schedule_slot") or f"manual-{int(time.time() // 60)}"
    )
    job_request = await jobs.create_request(
        idempotency_key=f"tag-audit:{strategy}:{total_count}:{schedule_slot}",
        kind="maintenance.tag_audit.run",
        scope={"kind": "system_maintenance"},
        payload={
            "strategy": strategy,
            "total_count": total_count,
            "provider_id": provider_id,
            "requested_by": "webui",
        },
    )
    run = await jobs.schedule_run(
        request_id=job_request.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=0,
        cursor={"phase": "queued", "processed": 0},
    )
    return jsonify({
        "ok": True,
        "accepted": True,
        "request_id": job_request.request_id,
        "job_id": run.run_id,
        "status": run.status,
    }), 202


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


async def _resolve_audit_suggestion(c, suggestion_id: int, decision: str) -> dict:
    gateway = getattr(c, "write_gateway", None)
    if gateway is None:
        return {"error": "write_gateway_unavailable"}

    def _resolve(connection):
        row = connection.execute(
            "SELECT id, status FROM tag_audit_suggestions WHERE id=?",
            (int(suggestion_id),),
        ).fetchone()
        if row is None:
            return {"error": f"Suggestion {suggestion_id} not found"}
        if row[1] != "pending":
            return {"error": f"Suggestion already {row[1]}"}
        if decision == "approve":
            return {
                "error": "scope_migration_required",
                "message": "Legacy unscoped Tag audit suggestions cannot be applied to scoped_tags.",
            }
        connection.execute(
            """UPDATE tag_audit_suggestions
               SET status='rejected', resolved_at=?
               WHERE id=? AND status='pending'""",
            (time.time(), int(suggestion_id)),
        )
        return {
            "suggestion_id": int(suggestion_id),
            "decision": "reject",
            "status": "rejected",
        }

    return await gateway.coordinator.transaction(
        _resolve,
        actor="webui.tag_audit.resolve",
    )


@tags_bp.route("/audit/resolve", methods=["POST"])
@require_auth
async def resolve_audit_suggestion():
    """批准或拒绝审计建议。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    suggestion_id = body.get("suggestion_id")
    decision = body.get("decision")
    if not suggestion_id or decision not in ("approve", "reject"):
        return jsonify({"error": "suggestion_id and decision (approve/reject) required"}), 400
    result = await _resolve_audit_suggestion(c, int(suggestion_id), decision)
    status = 409 if result.get("error") == "scope_migration_required" else 200
    return jsonify(result), status


@tags_bp.route("/audit/resolve-batch", methods=["POST"])
@require_auth
async def resolve_audit_batch():
    """批量处理审计建议。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    items = body.get("items", [])
    if not items:
        ids = body.get("suggestion_ids", [])
        decision = body.get("decision")
        if ids and decision:
            items = [{"id": sid, "decision": decision} for sid in ids]
    if not items:
        return jsonify({"error": "items or suggestion_ids+decision required"}), 400

    results = []
    for item in items:
        sid = item.get("id")
        dec = item.get("decision")
        if sid and dec in ("approve", "reject"):
            results.append(await _resolve_audit_suggestion(c, int(sid), dec))
    return jsonify({"processed": len(results), "results": results})


def _clamp_batch_size(value: object, *, default: int = 20, maximum: int = 50) -> int:
    try:
        size = int(value) if value is not None else default
    except (TypeError, ValueError):
        size = default
    return max(1, min(maximum, size))


def _untagged_memory_count(conn, min_length: int = 10, *, strict_scoped: bool = False) -> int:
    if not strict_scoped:
        return int(conn.execute(
            """SELECT COUNT(*) FROM memories m
               WHERE NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id=m.id)
                 AND LENGTH(COALESCE(m.content, '')) >= ?""",
            (min_length,),
        ).fetchone()[0])
    return int(conn.execute(
        """SELECT COUNT(*) FROM memories m
           WHERE NOT EXISTS (
               SELECT 1 FROM scoped_memory_tags smt WHERE smt.memory_id = m.id
           )
           AND EXISTS (
               SELECT 1 FROM domain_outbox o
               WHERE o.aggregate_kind='memory'
                 AND o.aggregate_id=CAST(m.id AS TEXT)
                 AND o.event_type='memory.created'
           )
           AND m.resolution_state='resolved'
           AND COALESCE(m.quarantine, 0)=0
           AND LENGTH(COALESCE(m.content, '')) >= ?""",
        (min_length,),
    ).fetchone()[0])


def _load_untagged_memory_batch(
    conn, limit: int, min_length: int = 10, *, strict_scoped: bool = False
) -> list:
    if not strict_scoped:
        return [
            (*row, None)
            for row in conn.execute(
                """SELECT m.id, m.content, m.sender_name FROM memories m
                   WHERE NOT EXISTS (
                       SELECT 1 FROM memory_tags mt WHERE mt.memory_id=m.id
                   )
                     AND LENGTH(COALESCE(m.content, '')) >= ?
                   ORDER BY m.id DESC LIMIT ?""",
                (min_length, limit),
            ).fetchall()
        ]
    return conn.execute(
        """SELECT m.id, m.content, m.sender_name, o.payload_json
           FROM memories m
           JOIN domain_outbox o
             ON o.aggregate_kind='memory'
            AND o.aggregate_id=CAST(m.id AS TEXT)
            AND o.event_type='memory.created'
           WHERE NOT EXISTS (
               SELECT 1 FROM scoped_memory_tags smt WHERE smt.memory_id = m.id
           )
           AND LENGTH(COALESCE(m.content, '')) >= ?
           AND m.resolution_state='resolved'
           AND COALESCE(m.quarantine, 0)=0
           ORDER BY m.id DESC LIMIT ?""",
        (min_length, limit),
    ).fetchall()


async def iter_batch_extract_events(
    c,
    batch_size: int,
    *,
    tag_write_policy: str = "missing_only",
    skip_short_min_length: int = 10,
    runtime_budget_seconds: float = 45.0,
    batch_timeout_seconds: float = 40.0,
):
    """生成单轮 Tag 提取进度事件。

    注意：这是 WebUI 的一次 HTTP/SSE 请求，不是后台长任务。运行时存在约 60s 的
    chunked 连接上限，所以这里每次只处理一批，并在安全时间窗内正常结束；前端可
    根据 remaining/partial 继续发起下一轮，避免 ERR_INCOMPLETE_CHUNKED_ENCODING。
    """
    if not getattr(c, "tag_extractor", None):
        yield {"error": "Tag extractor not configured", "done": True}
        return
    if tag_write_policy != "missing_only":
        yield {"error": "全库补提取只允许 tag_write_policy=missing_only；append/replace 必须在选中范围内执行。", "done": True}
        return

    conn = c.db.conn
    strict_scoped = getattr(c, "write_gateway", None) is not None
    total_remaining_before = _untagged_memory_count(
        conn, skip_short_min_length, strict_scoped=strict_scoped
    )
    if total_remaining_before == 0:
        yield {
            "progress": 1.0,
            "processed": 0,
            "total": 0,
            "tagged": 0,
            "errors": 0,
            "remaining": 0,
            "done": True,
            "message": "所有可提取记忆均已有标签",
        }
        return

    rows = _load_untagged_memory_batch(
        conn, batch_size, skip_short_min_length, strict_scoped=strict_scoped
    )
    selected = len(rows)
    yield {
        "progress": 0,
        "processed": 0,
        "total": total_remaining_before,
        "selected": selected,
        "tagged": 0,
        "errors": 0,
        "remaining": total_remaining_before,
        "message": f"本轮将处理 {selected} 条，当前剩余 {total_remaining_before} 条未标注记忆",
    }

    processed = tagged = errors = 0
    started = time.monotonic()
    messages = []
    for mem_id, content, sender_name, payload_json in rows:
        try:
            scope = json.loads(payload_json).get("scope")
        except (TypeError, ValueError, json.JSONDecodeError):
            scope = None
        if strict_scoped and not isinstance(scope, dict):
            errors += 1
            continue
        message = {
            "id": mem_id,
            "content": (content or "")[:800],
            "sender": sender_name or "",
        }
        if isinstance(scope, dict):
            message["scope"] = scope
        messages.append(message)

    if messages:
        try:
            remaining_budget = max(1.0, runtime_budget_seconds - (time.monotonic() - started))
            timeout = max(1.0, min(batch_timeout_seconds, remaining_budget))
            result = await asyncio.wait_for(
                tag_memory_batch(
                    c.db,
                    getattr(c, "embedding_service", None),
                    c.tag_extractor,
                    messages,
                    tag_batch_size=batch_size,
                    tag_write_policy=tag_write_policy,
                    skip_short_min_length=skip_short_min_length,
                    write_gateway=getattr(c, "write_gateway", None),
                ),
                timeout=timeout,
            )
            processed = int(result.get("processed", 0))
            tagged = int(result.get("tagged", 0))
            errors = int(result.get("errors", 0))
        except asyncio.TimeoutError:
            yield {
                "error": f"本轮 Tag LLM 提取超过 {int(batch_timeout_seconds)} 秒，已安全停止。请调小 tag_batch_size 后重试。",
                "progress": round(processed / total_remaining_before, 3),
                "processed": processed,
                "total": total_remaining_before,
                "tagged": tagged,
                "errors": selected,
                "remaining": total_remaining_before,
                "done": True,
            }
            return
        except Exception as exc:
            yield {
                "error": f"Tag LLM 批量提取失败: {type(exc).__name__}: {exc}",
                "progress": round(processed / total_remaining_before, 3),
                "processed": processed,
                "total": total_remaining_before,
                "tagged": tagged,
                "errors": selected,
                "remaining": total_remaining_before,
                "done": True,
            }
            return

    remaining_after = _untagged_memory_count(
        conn, skip_short_min_length, strict_scoped=strict_scoped
    )
    partial = remaining_after > 0
    yield {
        "progress": round((total_remaining_before - remaining_after) / total_remaining_before, 3) if total_remaining_before else 1.0,
        "processed": processed,
        "total": total_remaining_before,
        "selected": selected,
        "tagged": tagged,
        "errors": errors,
        "remaining": remaining_after,
        "partial": partial,
        "done": True,
        "message": (
            f"本轮完成：处理 {processed} 条，写入标签 {tagged} 条，剩余 {remaining_after} 条。"
            if partial else
            f"本轮完成：处理 {processed} 条，写入标签 {tagged} 条，未标注队列已清空。"
        ),
    }


@tags_bp.route("/batch-extract", methods=["POST"])
@require_auth
async def batch_extract_tags():
    """Queue one bounded Tag backfill batch as a durable job."""
    c = get_container()
    try:
        tag_options = normalize_tag_execution_options(request.args)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if tag_options["tag_write_policy"] != "missing_only":
        return jsonify({
            "ok": False,
            "error": "durable tag backfill only supports missing_only",
        }), 400
    jobs = getattr(c, "durable_jobs", None)
    if jobs is None:
        return jsonify({"ok": False, "error": "durable_jobs_unavailable"}), 503
    schedule_slot = str(
        request.args.get("schedule_slot") or f"manual-{int(time.time() // 60)}"
    )
    request_record = await jobs.create_request(
        idempotency_key=f"tag-backfill:{schedule_slot}:{tag_options['tag_batch_size']}",
        kind="maintenance.tag_backfill.run",
        scope={"kind": "system_maintenance"},
        payload={
            "batch_size": tag_options["tag_batch_size"],
            "skip_short_min_length": tag_options["skip_short_min_length"],
            "requested_by": "webui",
        },
    )
    run = await jobs.schedule_run(
        request_id=request_record.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=0,
        cursor={"phase": "queued", "after_id": 0},
    )
    return jsonify({
        "ok": True,
        "accepted": True,
        "request_id": request_record.request_id,
        "job_id": run.run_id,
        "status": run.status,
    }), 202


_import_lock = asyncio.Lock()
