"""Memories Blueprint — 记忆查询、导入、统计"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

try:
    from quart import Blueprint, current_app, jsonify, request
except ImportError:  # pragma: no cover - 本地单测可能注入不完整 fake Quart
    from types import SimpleNamespace
    from quart import Blueprint, jsonify, request

    try:
        from quart import current_app
    except ImportError:
        current_app = SimpleNamespace(extensions={})

from ..api_contract import (
    current_runtime_scope,
    error_payload,
    mutation_response,
    not_found_payload,
    page_response,
    scope_matches_memory_row,
)
from ..container import get_container
from ..middleware.auth import require_auth
from ..tag_execution import normalize_tag_execution_options

try:
    from ...engine.db.memory_repo import MemoryRevisionConflict
    from ...services.memory_mutations import (
        MemoryMutationGateway,
        MemoryMutationTarget,
        read_memory_tag_state,
    )
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from engine.db.memory_repo import MemoryRevisionConflict
    from services.memory_mutations import (
        MemoryMutationGateway,
        MemoryMutationTarget,
        read_memory_tag_state,
    )

memories_bp = Blueprint("memories", __name__, url_prefix="/api")

_import_preflights: dict[str, dict] = {}


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


def _api_composition():
    return current_app.extensions.get("wave_api_contract", {})


def _memory_columns(conn) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)").fetchall()}


def _memory_row(conn, memory_id: int) -> dict | None:
    columns = _memory_columns(conn)
    selected = [
        name for name in (
            "id", "content", "sender_id", "sender_name", "group_id", "source",
            "timestamp", "importance", "access_count", "bot_id", "session_id",
            "visibility", "resolution_state", "version",
        ) if name in columns
    ]
    if "id" not in selected:
        return None
    cursor = conn.execute(
        f"SELECT {', '.join(selected)} FROM memories WHERE id=? LIMIT 1", (memory_id,)
    )
    row = cursor.fetchone()
    return dict(zip(selected, row)) if row else None


def _request_scope():
    return current_runtime_scope(_api_composition().get("request_scope_provider"))


def _memory_ref_item(item: dict, *, existing_ref: str | None = None) -> dict:
    result = dict(item)
    scope = _request_scope()
    refs = _api_composition().get("object_refs")
    if (
        refs is None
        or scope is None
        or not scope_matches_memory_row(scope, result)
        or not isinstance(result.get("version"), int)
    ):
        return result
    ref = existing_ref or refs.issue(
        kind="memory",
        locator=result["id"],
        scope=scope,
        revision=result["version"],
    )
    scope_query = {
        "bot_id": scope.bot_id,
        "visibility": scope.visibility,
    }
    if scope.session is not None:
        scope_query["session_id"] = scope.session.id
    if scope.subject_principal_id:
        scope_query["subject_principal_id"] = scope.subject_principal_id
    result["ref"] = ref
    result["object_ref"] = {
        "ref": ref,
        "kind": "memory",
        "locator": result["id"],
        "scope_key": scope.session.id if scope.session else scope.bot_id,
        "scope_query": dict(scope_query),
        "version": result["version"],
    }
    detail_query = {"ref": ref, **scope_query}
    result["detail_url"] = f"/api/memories/{result['id']}?{urlencode(detail_query)}"
    result["mutation_url"] = result["detail_url"]
    return result


def _resolve_memory_ref_with_state(
    conn,
    *,
    ref: str | None = None,
    memory_id: int | None = None,
):
    ref = ref or request.args.get("ref")
    refs = _api_composition().get("object_refs")
    scope = _request_scope()
    if refs is None:
        return None, None, "not-found"
    binding, state = refs.resolve_with_state(
        ref,
        kind="memory",
        locator=memory_id,
        request_scope=scope,
    )
    if binding is None:
        return None, None, state
    try:
        resolved_id = int(binding.locator)
    except (TypeError, ValueError):
        return None, None, "not-found"
    row = _memory_row(conn, resolved_id)
    if row is None:
        return None, None, "not-found"
    if row.get("version") != binding.revision:
        return None, None, "version-stale"
    if scope is None or not scope_matches_memory_row(scope, row):
        return None, None, "scope-mismatch"
    return binding, row, "ready"


def _resolve_memory_ref(conn, memory_id: int, *, ref: str | None = None):
    binding, row, state = _resolve_memory_ref_with_state(
        conn,
        ref=ref,
        memory_id=memory_id,
    )
    return (binding, row) if state == "ready" else (None, None)


def _resolve_batch_refs(
    conn, values
) -> tuple[tuple[object, list[MemoryMutationTarget]] | None, tuple[dict, int] | None]:
    """Resolve selected refs to one Scope plus immutable ID/revision job targets."""
    if not isinstance(values, list) or not values:
        return None, (error_payload("object_refs_required", "Object references are required"), 400)
    scope = None
    resolved: list[MemoryMutationTarget] = []
    seen: set[int] = set()
    for value in values:
        if not isinstance(value, dict):
            return None, (error_payload("object_refs_required", "Object references are required"), 400)
        memory_id = _safe_int(value.get("id"), 0)
        ref = value.get("ref")
        if memory_id <= 0 or not isinstance(ref, str):
            return None, (error_payload("object_refs_required", "Object references are required"), 400)
        binding, row = _resolve_memory_ref(conn, memory_id, ref=ref)
        if binding is None or row is None:
            return None, (not_found_payload(), 404)
        if scope is None:
            scope = binding.scope
        elif binding.scope != scope:
            return None, (error_payload("mixed_runtime_scopes", "Batch targets must share one RuntimeScope"), 400)
        if memory_id not in seen:
            resolved.append(
                MemoryMutationTarget(memory_id=memory_id, revision=int(binding.revision))
            )
            seen.add(memory_id)
    return (scope, resolved), None


def _legacy_batch_error(body: dict) -> tuple[dict, int] | None:
    if body.get("ids") is not None and body.get("refs") is None:
        return (
            error_payload(
                "memory_object_ref_migration_required",
                "Legacy bare memory IDs are no longer accepted; reload and submit ObjectRefs",
            ),
            410,
        )
    return None


def _memory_mutation_gateway(container):
    write_gateway = getattr(container, "write_gateway", None)
    if write_gateway is None:
        return None
    try:
        return MemoryMutationGateway(write_gateway)
    except (TypeError, ValueError):
        return None


async def _enqueue_memory_job(
    container,
    *,
    kind: str,
    scope,
    targets: list[MemoryMutationTarget],
    options: dict | None = None,
    schedule_slot: str | None = None,
):
    jobs = getattr(container, "durable_jobs", None)
    enqueue = getattr(jobs, "enqueue", None)
    if not callable(enqueue):
        return None
    payload = {
        "scope": scope.to_dict(),
        "targets": [target.to_dict() for target in targets],
        **dict(options or {}),
        "requested_by": "webui",
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    slot = str(schedule_slot or f"webui:{kind}:{digest}")
    return await enqueue(
        idempotency_key=f"{kind}:{digest}",
        kind=kind,
        scope=scope.to_dict(),
        payload=payload,
        schedule_slot=slot,
        cursor={"phase": "queued", "processed": 0, "total": len(targets)},
    )


@memories_bp.route("/memories/legacy/audit", methods=["GET"])
@require_auth
async def list_legacy_memories_audit():
    """分页读取无法证明完整 RuntimeScope 的旧记忆；永不签发 ObjectRef 或写能力。"""
    c = get_container()
    conn = c.db.conn
    columns = _memory_columns(conn)
    limit = max(1, min(200, _safe_int(request.args.get("limit", 25), 25)))
    offset = max(0, _safe_int(request.args.get("offset", 0), 0))
    required = {"id", "content"}
    if not required.issubset(columns):
        payload = page_response([], total=0, limit=limit, offset=offset)
        payload.update({"legacy": True, "readonly": True, "scope_status": "unresolved_legacy"})
        return jsonify(payload)

    unresolved = []
    if "resolution_state" in columns:
        unresolved.append("COALESCE(resolution_state, '') != 'resolved'")
    if "bot_id" in columns:
        unresolved.append("COALESCE(bot_id, '') = ''")
    if "session_id" in columns:
        unresolved.append("COALESCE(session_id, '') = ''")
    if "visibility" in columns:
        unresolved.append("COALESCE(visibility, '') != 'group'")
    where = ["(" + " OR ".join(unresolved) + ")"] if unresolved else ["1=1"]
    params: list = []
    search = str(request.args.get("search") or "").strip()
    if search:
        where.append("content LIKE ?")
        params.append(f"%{search}%")
    source = str(request.args.get("source") or "").strip()
    if source and "source" in columns:
        where.append("source=?")
        params.append(source)
    where_sql = " AND ".join(where)
    total = int(conn.execute(f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params).fetchone()[0])
    selected = [name for name in (
        "id", "content", "sender_id", "sender_name", "group_id", "source", "timestamp",
        "importance", "bot_id", "session_id", "visibility", "resolution_state", "quarantine",
    ) if name in columns]
    cursor = conn.execute(
        f"SELECT {', '.join(selected)} FROM memories WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    names = [str(column[0]) for column in cursor.description or ()]
    items = []
    for row in cursor.fetchall():
        item = dict(zip(names, row))
        item.update({
            "legacy": True,
            "readonly": True,
            "scope_status": "unresolved_legacy",
            "scope_reason": "complete_runtime_scope_unavailable",
            "object_ref": None,
            "actions": {},
        })
        items.append(item)
    payload = page_response(items, total=total, limit=limit, offset=offset)
    payload.update({
        "legacy": True,
        "readonly": True,
        "scope": None,
        "scope_status": "unresolved_legacy",
        "reason_code": "complete_runtime_scope_unavailable",
    })
    return jsonify(payload)


@memories_bp.route("/memories", methods=["GET"])
@require_auth
async def list_memories():
    """分页查看真实记忆；page/size 仅在 HTTP 边界换算为 limit/offset。"""
    c = get_container()
    page = request.args.get("page")
    size = request.args.get("size")
    if page is not None or size is not None:
        limit = max(1, min(200, _safe_int(size or 30, 30)))
        offset = (max(1, _safe_int(page or 1, 1)) - 1) * limit
    else:
        limit = max(1, min(200, _safe_int(request.args.get("limit", 50), 50)))
        offset = max(0, _safe_int(request.args.get("offset", 0), 0))

    conn = c.db.conn
    columns = _memory_columns(conn)
    scope = _request_scope()
    if scope is None or scope.session is None:
        return jsonify(page_response(
            [],
            total=None,
            limit=limit,
            offset=offset,
            unavailable_reason="scope_required",
        ))
    required_scope_columns = {"bot_id", "session_id", "visibility", "group_id", "resolution_state"}
    if not required_scope_columns.issubset(columns):
        return jsonify(page_response(
            [],
            total=None,
            limit=limit,
            offset=offset,
            unavailable_reason="scope_schema_unavailable",
        ))
    where = [
        "bot_id = ?",
        "session_id = ?",
        "visibility = ?",
        "group_id = ?",
        "resolution_state = 'resolved'",
    ]
    params = [
        scope.bot_id,
        scope.session.id,
        scope.visibility,
        scope.session.conversation_id,
    ]
    before_id = request.args.get("before_id")
    if before_id:
        where.append("id < ?")
        params.append(_safe_int(before_id, 0))
        offset = 0
    for name in ("source", "sender_id", "group_id"):
        value = request.args.get(name)
        if value and name in columns:
            where.append(f"{name} = ?")
            params.append(value)
    sender = request.args.get("sender")
    if sender and "sender_name" in columns:
        where.append("sender_name = ?")
        params.append(sender)
    bot_id = request.args.get("bot_id")
    if bot_id and "bot_id" in columns:
        where.append("bot_id = ?")
        params.append(bot_id)
    search = (request.args.get("search") or "").strip()
    if search:
        where.append("content LIKE ?")
        params.append(f"%{search}%")
    has_vector = request.args.get("has_vector")
    if has_vector == "true":
        where.append("vector IS NOT NULL")
    elif has_vector == "false":
        where.append("vector IS NULL")
    has_tags = request.args.get("has_tags")
    if has_tags == "true":
        where.append("EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = memories.id)")
    elif has_tags == "false":
        where.append("NOT EXISTS (SELECT 1 FROM memory_tags mt WHERE mt.memory_id = memories.id)")

    where_sql = " AND ".join(where)
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM memories WHERE {where_sql}", params
    ).fetchone()[0])
    selected = [
        name for name in (
            "id", "content", "sender_id", "sender_name", "group_id", "source",
            "timestamp", "importance", "access_count", "bot_id", "session_id",
            "visibility", "resolution_state", "version",
        ) if name in columns
    ]
    select_sql = ", ".join(selected + ["vector IS NOT NULL AS has_vector"])
    cursor = conn.execute(
        f"SELECT {select_sql} FROM memories WHERE {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    names = [str(column[0]) for column in cursor.description or ()]
    items = []
    for row in cursor.fetchall():
        item = dict(zip(names, row))
        item["has_vector"] = bool(item.get("has_vector"))
        items.append(_memory_ref_item(item))
    return jsonify(page_response(items, total=total, limit=limit, offset=offset))


@memories_bp.route("/memories/senders", methods=["GET"])
@require_auth
async def list_senders():
    """发送者列表（按记忆数排序，供筛选下拉）。"""
    c = get_container()
    limit = max(1, min(500, _safe_int(request.args.get("limit", 100), 100)))
    scope = _request_scope()
    if scope is None or scope.session is None:
        return jsonify({"senders": [], "source": {"status": "unknown", "reason_code": "scope_required"}})
    rows = c.db.conn.execute(
        """SELECT sender_name, COUNT(*) AS cnt FROM memories
           WHERE sender_name IS NOT NULL AND sender_name != ''
             AND bot_id=? AND session_id=? AND visibility=? AND group_id=?
             AND resolution_state='resolved'
           GROUP BY sender_name ORDER BY cnt DESC LIMIT ?""",
        (
            scope.bot_id,
            scope.session.id,
            scope.visibility,
            scope.session.conversation_id,
            limit,
        ),
    ).fetchall()
    return jsonify({
        "senders": [{"name": r[0], "count": r[1]} for r in rows],
        "source": {"status": "healthy", "reason_code": None},
    })


def _memory_deep_link_error(state: str):
    # Detail reads intentionally collapse missing, scope mismatch and stale
    # revisions into one non-leaking response. Mutations retain explicit codes.
    return jsonify(not_found_payload()), 404


@memories_bp.route("/memories/resolve", methods=["GET"])
@require_auth
async def resolve_memory():
    """仅凭 opaque ObjectRef 与显式当前 Scope 解析记忆详情。"""
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    _, item, state = _resolve_memory_ref_with_state(get_container().db.conn)
    if item is None:
        return _memory_deep_link_error(state)
    return jsonify({
        "item": _memory_ref_item(item, existing_ref=request.args.get("ref")),
        "resolution": {"state": "ready"},
    })


@memories_bp.route("/memories/<int:memory_id>", methods=["GET"])
@require_auth
async def get_memory(memory_id: int):
    """仅通过服务端签发且与当前 Scope/revision 相符的 ObjectRef 读取详情。"""
    conn = get_container().db.conn
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    _, item, state = _resolve_memory_ref_with_state(conn, memory_id=memory_id)
    if item is None:
        return _memory_deep_link_error(state)
    return jsonify({
        "item": _memory_ref_item(item, existing_ref=request.args.get("ref")),
        "resolution": {"state": "ready"},
    })


@memories_bp.route("/memories/<int:memory_id>", methods=["PUT"])
@require_auth
async def update_memory(memory_id: int):
    """使用 scoped ObjectRef 乐观更新记忆并推进 revision。"""
    c = get_container()
    conn = c.db.conn
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, item = _resolve_memory_ref(conn, memory_id)
    if binding is None or item is None:
        return jsonify(not_found_payload()), 404
    body = await request.get_json(silent=True) or {}
    fields = {
        name: body[name]
        for name in ("content", "importance")
        if name in body
    }
    if not fields:
        return jsonify(error_payload("invalid_request", "No mutable fields were supplied")), 400
    gateway = _memory_mutation_gateway(c)
    if gateway is None:
        return jsonify(error_payload(
            "memory_mutation_gateway_unavailable",
            "The coordinated memory mutation gateway is unavailable",
            retryable=True,
        )), 503
    try:
        result = await gateway.update_memory(
            scope=binding.scope,
            target=MemoryMutationTarget(memory_id=memory_id, revision=binding.revision),
            **fields,
        )
    except MemoryRevisionConflict:
        return jsonify(not_found_payload()), 404
    except ValueError as exc:
        return jsonify(error_payload("invalid_request", str(exc))), 400
    refreshed = _memory_row(conn, memory_id)
    if refreshed is None:
        return jsonify(not_found_payload()), 404
    return jsonify(
        mutation_response(
            operation_kind="memory.update",
            operation_id=result.operation_id,
            status="succeeded",
            revision=result.revision,
            item=_memory_ref_item(refreshed),
            include_item=True,
        )
    )


@memories_bp.route("/memories/<int:memory_id>", methods=["DELETE"])
@require_auth
async def delete_memory(memory_id: int):
    """使用 scoped ObjectRef 删除单条记忆。"""
    c = get_container()
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, item = _resolve_memory_ref(c.db.conn, memory_id)
    if binding is None or item is None:
        return jsonify(not_found_payload()), 404
    gateway = _memory_mutation_gateway(c)
    if gateway is None:
        return jsonify(error_payload(
            "memory_mutation_gateway_unavailable",
            "The coordinated memory mutation gateway is unavailable",
            retryable=True,
        )), 503
    try:
        result = await gateway.delete_memories(
            scope=binding.scope,
            targets=[MemoryMutationTarget(memory_id=memory_id, revision=binding.revision)],
        )
    except MemoryRevisionConflict:
        return jsonify(not_found_payload()), 404
    return jsonify(
        mutation_response(
            operation_kind="memory.delete",
            operation_id=result.operation_id,
            status="succeeded",
            revision=result.revision,
        )
    )


@memories_bp.route("/memories/<int:memory_id>/re-embed", methods=["POST"])
@require_auth
async def re_embed_memory(memory_id: int):
    """重新向量化单条记忆。"""
    c = get_container()
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, detail = _resolve_memory_ref(c.db.conn, memory_id)
    if binding is None or detail is None:
        return jsonify(not_found_payload()), 404
    envelope = await _enqueue_memory_job(
        c,
        kind="memory.reembed.v1",
        scope=binding.scope,
        targets=[MemoryMutationTarget(memory_id=memory_id, revision=binding.revision)],
        schedule_slot=request.args.get("schedule_slot"),
    )
    if envelope is None:
        return jsonify(error_payload(
            "durable_jobs_unavailable",
            "Durable memory jobs are unavailable",
            retryable=True,
        )), 503
    return jsonify(envelope.to_dict()), 202


@memories_bp.route("/memories/batch/delete", methods=["POST"])
@require_auth
async def batch_delete_memories():
    """批量删除记忆。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    legacy_error = _legacy_batch_error(body)
    if legacy_error is not None:
        payload, status = legacy_error
        return jsonify(payload), status
    resolved, batch_error = _resolve_batch_refs(c.db.conn, body.get("refs"))
    if batch_error is not None:
        payload, status = batch_error
        return jsonify(payload), status
    assert resolved is not None
    scope, targets = resolved
    gateway = _memory_mutation_gateway(c)
    if gateway is None:
        return jsonify(error_payload(
            "memory_mutation_gateway_unavailable",
            "The coordinated memory mutation gateway is unavailable",
            retryable=True,
        )), 503
    try:
        result = await gateway.delete_memories(scope=scope, targets=targets)
    except MemoryRevisionConflict:
        return jsonify(not_found_payload()), 404
    return jsonify(mutation_response(
        operation_kind="memory.batch.delete",
        operation_id=result.operation_id,
        status="succeeded",
        revision=None,
        item={"deleted": len(result.targets)},
        include_item=True,
    ))


@memories_bp.route("/memories/batch/re-embed", methods=["POST"])
@require_auth
async def batch_re_embed():
    """批量重新向量化（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    legacy_error = _legacy_batch_error(body)
    if legacy_error is not None:
        payload, status = legacy_error
        return jsonify(payload), status
    resolved, batch_error = _resolve_batch_refs(c.db.conn, body.get("refs"))
    if batch_error is not None:
        payload, status = batch_error
        return jsonify(payload), status
    assert resolved is not None
    scope, targets = resolved
    envelope = await _enqueue_memory_job(
        c,
        kind="memory.batch.reembed.v1",
        scope=scope,
        targets=targets,
        schedule_slot=body.get("schedule_slot"),
    )
    if envelope is None:
        return jsonify(error_payload(
            "durable_jobs_unavailable",
            "Durable memory jobs are unavailable",
            retryable=True,
        )), 503
    return jsonify(envelope.to_dict()), 202


@memories_bp.route("/memories/batch/extract-tags", methods=["POST"])
@require_auth
async def batch_extract_tags_for_ids():
    """对选中记忆批量提取 Tag（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    legacy_error = _legacy_batch_error(body)
    if legacy_error is not None:
        payload, status = legacy_error
        return jsonify(payload), status
    try:
        tag_options = normalize_tag_execution_options(body)
    except ValueError as exc:
        return jsonify(error_payload("invalid_tag_options", str(exc))), 400
    resolved, batch_error = _resolve_batch_refs(c.db.conn, body.get("refs"))
    if batch_error is not None:
        payload, status = batch_error
        return jsonify(payload), status
    assert resolved is not None
    scope, targets = resolved
    envelope = await _enqueue_memory_job(
        c,
        kind="memory.batch.extract_tags.v1",
        scope=scope,
        targets=targets,
        options={
            "tag_batch_size": tag_options["tag_batch_size"],
            "tag_write_policy": tag_options["tag_write_policy"],
            "skip_short_min_length": tag_options["skip_short_min_length"],
        },
        schedule_slot=body.get("schedule_slot"),
    )
    if envelope is None:
        return jsonify(error_payload(
            "durable_jobs_unavailable",
            "Durable memory jobs are unavailable",
            retryable=True,
        )), 503
    return jsonify(envelope.to_dict()), 202



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
    """旧裸 PATCH 永久停用；正式记忆变更只允许带 ObjectRef 的 PUT。"""
    return jsonify(error_payload("legacy_mutation_disabled", "Bare PATCH memory mutation is no longer supported")), 410


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


@memories_bp.route("/memories/<int:memory_id>/similar", methods=["GET"])
@require_auth
async def get_similar_memories(memory_id: int):
    """基于内存 C-HNSW 检索单条记忆的 Top 3 相似推荐（≤15ms 高性能，零 SQLite 向量扫描）。"""
    c = get_container()
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, detail = _resolve_memory_ref(c.db.conn, memory_id)
    if binding is None or detail is None:
        return jsonify(not_found_payload()), 404
    row = c.db.conn.execute("SELECT vector FROM memories WHERE id=?", (memory_id,)).fetchone()
    if not row or not row[0]:
        return jsonify({"items": [], "reason": "no_vector"})

    vec_blob = row[0]
    import struct
    try:
        num_floats = len(vec_blob) // 4
        vector = list(struct.unpack(f"{num_floats}f", vec_blob))
    except Exception as exc:
        return jsonify({"items": [], "reason": f"unpack_failed: {exc}"})

    if not getattr(c, "memory_index", None):
        return jsonify({"items": [], "reason": "hnsw_index_unavailable"})

    # 1. knn 快速查询
    try:
        candidates = c.memory_index.search(vector, k=5)  # 多取几条，过滤掉自身
    except Exception as exc:
        return jsonify({"items": [], "reason": f"hnsw_query_failed: {exc}"})

    similar_ids = []
    distances = {}
    for mid, dist in candidates:
        if mid != memory_id:
            similar_ids.append(mid)
            distances[mid] = dist
        if len(similar_ids) >= 3:
            break

    if not similar_ids:
        return jsonify({"items": []})

    # 2. SQLite 主键点查
    placeholders = ",".join("?" for _ in similar_ids)
    scope = binding.scope
    assert scope.session is not None
    rows = c.db.conn.execute(
        f"SELECT id, content, source FROM memories WHERE id IN ({placeholders}) "
        "AND bot_id=? AND session_id=? AND visibility=? AND group_id=? "
        "AND resolution_state='resolved'",
        similar_ids + [
            scope.bot_id,
            scope.session.id,
            scope.visibility,
            scope.session.conversation_id,
        ],
    ).fetchall()

    items = []
    for r in rows:
        mid = r[0]
        dist = distances.get(mid, 0.5)
        # 相似度 = 1.0 - 距离 (100% 格式展示)
        similarity = round((1.0 - float(dist)) * 100, 1)
        items.append({
            "id": mid,
            "content": r[1] or "",
            "source": r[2] or "",
            "similarity": similarity
        })

    # 按相似度降序排序
    items.sort(key=lambda x: x["similarity"], reverse=True)
    return jsonify({"items": items})


def _memory_tag_state_payload(conn, *, scope, memory_id: int) -> dict:
    state = read_memory_tag_state(conn, scope=scope, memory_id=memory_id)
    manual = state.get("manual")
    refs = _api_composition().get("object_refs")
    if isinstance(manual, dict) and refs is not None:
        correction_ref = refs.issue(
            kind="memory_tag_correction",
            locator=manual["correction_id"],
            scope=scope,
            revision=int(manual["revision"]),
        )
        manual["ref"] = correction_ref
        manual["object_ref"] = {
            "ref": correction_ref,
            "kind": "memory_tag_correction",
            "locator": manual["correction_id"],
            "scope_key": scope.session.id if scope.session else scope.bot_id,
            "version": int(manual["revision"]),
        }
    return state


@memories_bp.route("/memories/<int:memory_id>/tags", methods=["GET"])
@require_auth
async def get_memory_tag_state(memory_id: int):
    c = get_container()
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, item = _resolve_memory_ref(c.db.conn, memory_id)
    if binding is None or item is None:
        return jsonify(not_found_payload()), 404
    return jsonify({"item": _memory_tag_state_payload(c.db.conn, scope=binding.scope, memory_id=memory_id)})


@memories_bp.route("/memories/<int:memory_id>/tags/correction", methods=["POST"])
@require_auth
async def correct_memory_tags(memory_id: int):
    c = get_container()
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    binding, item = _resolve_memory_ref(c.db.conn, memory_id)
    if binding is None or item is None:
        return jsonify(not_found_payload()), 404
    body = await request.get_json(silent=True) or {}
    try:
        result = await _memory_mutation_gateway(c).correct_memory_tags(
            scope=binding.scope,
            target=MemoryMutationTarget(memory_id=memory_id, revision=int(binding.revision)),
            operation=body.get("operation"),
            tags=body.get("tags") if isinstance(body.get("tags"), list) else (),
            reason=body.get("reason"),
        )
    except ValueError as exc:
        return jsonify(error_payload("invalid_memory_tag_correction", str(exc))), 400
    except MemoryRevisionConflict:
        return jsonify(not_found_payload()), 404
    refreshed = _memory_row(c.db.conn, memory_id)
    if refreshed is None:
        return jsonify(not_found_payload()), 404
    state = _memory_tag_state_payload(c.db.conn, scope=binding.scope, memory_id=memory_id)
    return jsonify(mutation_response(
        operation_kind="memory.tags.correct",
        operation_id=result.operation_id,
        status="succeeded",
        revision=result.revision,
        item={"memory": _memory_ref_item(refreshed), "tags": state},
        include_item=True,
    ))


@memories_bp.route("/memories/<int:memory_id>/tags/correction/undo", methods=["POST"])
@require_auth
async def undo_memory_tag_correction(memory_id: int):
    c = get_container()
    memory_ref = request.args.get("ref")
    if not memory_ref:
        return jsonify(error_payload("object_ref_required", "Memory object reference is required")), 400
    binding, item = _resolve_memory_ref(c.db.conn, memory_id, ref=memory_ref)
    if binding is None or item is None:
        return jsonify(not_found_payload()), 404
    body = await request.get_json(silent=True) or {}
    correction_ref = body.get("correction_ref")
    refs = _api_composition().get("object_refs")
    correction_binding, _correction_state = refs.resolve_with_state(
        correction_ref,
        kind="memory_tag_correction",
        request_scope=binding.scope,
    ) if refs is not None else (None, "not-found")
    if correction_binding is None:
        return jsonify(not_found_payload()), 404
    try:
        result = await _memory_mutation_gateway(c).undo_memory_tag_correction(
            scope=binding.scope,
            target=MemoryMutationTarget(memory_id=memory_id, revision=int(binding.revision)),
            correction_id=str(correction_binding.locator),
            correction_revision=int(correction_binding.revision),
            reason=body.get("reason"),
        )
    except ValueError as exc:
        return jsonify(error_payload("invalid_memory_tag_correction_undo", str(exc))), 400
    except MemoryRevisionConflict:
        return jsonify(not_found_payload()), 404
    refreshed = _memory_row(c.db.conn, memory_id)
    if refreshed is None:
        return jsonify(not_found_payload()), 404
    state = _memory_tag_state_payload(c.db.conn, scope=binding.scope, memory_id=memory_id)
    return jsonify(mutation_response(
        operation_kind="memory.tags.undo",
        operation_id=result.operation_id,
        status="succeeded",
        revision=result.revision,
        item={"memory": _memory_ref_item(refreshed), "tags": state},
        include_item=True,
    ))


@memories_bp.route("/memories/<int:memory_id>/tags", methods=["POST"])
@memories_bp.route("/memories/<int:memory_id>/tags/<tag_name>", methods=["DELETE"])
@require_auth
async def legacy_memory_tag_mutation(memory_id: int, tag_name: str | None = None):
    del memory_id, tag_name
    if not request.args.get("ref"):
        return jsonify(error_payload("object_ref_required", "Object reference is required")), 400
    return jsonify(error_payload(
        "memory_tag_correction_required",
        "Legacy direct Tag mutation was removed; use the scoped correction endpoint with reason and ObjectRefs",
    )), 410


@memories_bp.route("/memories/import/sources", methods=["GET"])
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


@memories_bp.route("/import/preflight", methods=["POST"])
@require_auth
async def import_preflight():
    """Resolve a discovered source and issue a short-lived token for the exact import request."""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    source_id = str(body.get("source_id") or "").strip()
    if not source_id:
        return jsonify(error_payload("import_source_required", "Import source is required")), 400
    from ..source_discovery import SourceDiscovery

    discovery = SourceDiscovery()
    source = next((item for item in discovery.discover_all() if item.get("id") == source_id), None)
    if source is None:
        return jsonify(error_payload("import_source_not_found", "Import source was not found")), 404
    limit = max(1, min(_safe_int(body.get("limit", 2000), 2000), 50_000))
    progress = discovery.estimate_imported(source, c.db)
    target = str((source.get("adapter") or {}).get("target", "memories"))
    token = secrets.token_urlsafe(32)
    checked_at = time.time()
    request_payload = {
        "source_id": source_id,
        "limit": limit,
        "extract_tags": bool(body.get("extract_tags", True)),
        "tag_batch_size": max(1, min(_safe_int(body.get("tag_batch_size", 20), 20), 100)),
        "tag_write_policy": str(body.get("tag_write_policy") or "missing_only"),
    }
    _import_preflights[token] = {
        "expires_at": checked_at + 600.0,
        "request": request_payload,
    }
    return jsonify({
        "source": {
            "id": source_id,
            "name": source.get("name"),
            "description": source.get("description"),
            "count": int(source.get("count") or 0),
            "type": source.get("type"),
            "target": target,
        },
        "preview": {
            "total_count": int(source.get("count") or 0),
            "estimated_imported": int(progress.get("estimated_imported") or 0),
            "estimated_remaining": int(progress.get("estimated_remaining") or 0),
            "limit": limit,
            "re_embed": target == "memories",
            "extract_tags": request_payload["extract_tags"],
        },
        "preflight_token": token,
        "checked_at": checked_at,
        "source_status": "available",
    })


@memories_bp.route("/import/start", methods=["POST"])
@require_auth
async def import_start():
    """开始导入（SSE 流）。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    source = str(body.get("source") or "")
    source_id = str(body.get("source_id") or "")
    re_embed = body.get("re_embed", True)
    extract_tags = body.get("extract_tags", True)
    batch_size = _safe_int(body.get("batch_size", 20), 20)
    mode = "legacy"
    request_payload = {
        "mode": "legacy",
        "source": source,
        "re_embed": bool(re_embed),
        "extract_tags": bool(extract_tags),
        "batch_size": batch_size,
        "requested_by": "webui",
    }
    idempotency_source = f"legacy:{source}"
    if source_id:
        token = str(body.get("preflight_token") or "")
        preflight = _import_preflights.get(token)
        expected = preflight.get("request") if isinstance(preflight, dict) else None
        if (
            not isinstance(preflight, dict)
            or float(preflight.get("expires_at") or 0) < time.time()
            or not isinstance(expected, dict)
            or expected.get("source_id") != source_id
        ):
            return jsonify(error_payload("import_preflight_required", "A valid import preflight is required")), 409
        mode = "discovered_source"
        request_payload = {"mode": mode, **expected, "requested_by": "webui"}
        idempotency_source = f"source:{source_id}:{token}"

    jobs = getattr(c, "durable_jobs", None)
    if jobs is None:
        return jsonify({"ok": False, "error": "durable_jobs_unavailable"}), 503
    schedule_slot = str(body.get("schedule_slot") or f"manual-{int(time.time() // 60)}")
    request_record = await jobs.create_request(
        idempotency_key=f"import:{idempotency_source}:{schedule_slot}",
        kind="maintenance.import.run",
        scope={"kind": "system_maintenance"},
        payload=request_payload,
    )
    run = await jobs.schedule_run(
        request_id=request_record.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=0,
        cursor={"phase": "queued"},
    )
    return jsonify({
        "ok": False,
        "accepted": True,
        "request_id": request_record.request_id,
        "job_id": run.run_id,
        "status": run.status,
        "operation": {
            "id": run.run_id,
            "kind": "maintenance.import.run",
            "status": "queued" if run.status == "pending" else run.status,
        },
        "revision": None,
    }), 202


@memories_bp.route("/memories/import/run", methods=["POST"])
@memories_bp.route("/import/from-source", methods=["POST"])
@require_auth
async def import_from_source():
    """从指定数据源导入（SSE 流）。"""
    c = get_container()
    source_id = request.args.get("source_id", "")
    limit = _safe_int(request.args.get("limit", 5000), 5000)
    try:
        tag_options = normalize_tag_execution_options(request.args, defaults={"extract_tags": False})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    extract_tags = tag_options["extract_tags"]
    tag_batch_size = tag_options["tag_batch_size"]
    tag_write_policy = tag_options["tag_write_policy"]

    from ..source_discovery import SourceDiscovery
    discovery = SourceDiscovery()
    all_sources = discovery.discover_all()
    source = next((s for s in all_sources if s["id"] == source_id), None)

    if not source:
        return jsonify({"error": f"Source not found: {source_id}"}), 404

    jobs = getattr(c, "durable_jobs", None)
    if jobs is None:
        return jsonify({"ok": False, "error": "durable_jobs_unavailable"}), 503
    schedule_slot = str(
        request.args.get("schedule_slot") or f"manual-{int(time.time() // 60)}"
    )
    request_record = await jobs.create_request(
        idempotency_key=f"import:source:{source_id}:{schedule_slot}",
        kind="maintenance.import.run",
        scope={"kind": "system_maintenance"},
        payload={
            "mode": "discovered_source",
            "source_id": source_id,
            "limit": limit,
            "extract_tags": extract_tags,
            "tag_batch_size": tag_batch_size,
            "tag_write_policy": tag_write_policy,
            "requested_by": "webui",
        },
    )
    run = await jobs.schedule_run(
        request_id=request_record.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=0,
        cursor={"phase": "queued"},
    )
    return jsonify({
        "ok": True,
        "accepted": True,
        "request_id": request_record.request_id,
        "job_id": run.run_id,
        "status": run.status,
    }), 202


@memories_bp.route("/memories/import/llm-extract", methods=["POST"])
@require_auth
async def legacy_import_llm_extract():
    """旧前端兼容：转发到当前批量 Tag 提取 SSE。"""
    from .tags import batch_extract_tags
    return await batch_extract_tags()


@memories_bp.route("/memories/import/llm-extract/stop", methods=["POST"])
@require_auth
async def legacy_import_llm_extract_stop():
    """旧前端兼容：当前批量 Tag 提取不支持安全中止，避免旧包触发 404。"""
    return jsonify({"ok": False, "message": "当前批量标签提取任务不支持安全中止"})
