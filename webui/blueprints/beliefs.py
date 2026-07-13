"""Beliefs Blueprint — 信念管理 CRUD + approve/archive (US-1.2)"""

from __future__ import annotations

import json
import time
from functools import wraps

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from ...engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError
except ImportError:  # pragma: no cover - plugin root may be imported directly
    from domain.scope import RuntimeScope, ScopeCodec, ScopeValidationError, SessionRef
    from engine.db.scoped_knowledge_repo import ScopedKnowledgeScopeError

beliefs_bp = Blueprint("beliefs", __name__, url_prefix="/api/beliefs")


def _table_exists(conn, table: str) -> bool:
    """检查表是否存在。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


# 需要排除的 archived_reason（身份污染 / 清理标记）
_EXCLUDED_REASONS = ("identity_roleplay_contamination", "identity_cleanup_full")
_VALID_BELIEF_TYPES = {"self_identity", "person_judgment", "world_view", "preference"}
_LEGACY_BELIEF_TYPE_MAP = {
    "self": "self_identity",
    "other": "person_judgment",
    "world": "world_view",
    "value": "preference",
}
_NEW_TO_LEGACY_BELIEF_TYPE_MAP = {v: k for k, v in _LEGACY_BELIEF_TYPE_MAP.items()}


def _normalize_belief_type(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _LEGACY_BELIEF_TYPE_MAP.get(raw, raw)


def _append_belief_type_filter(where_parts: list[str], params: list, belief_type) -> None:
    normalized = _normalize_belief_type(belief_type)
    if not normalized:
        return
    legacy = _NEW_TO_LEGACY_BELIEF_TYPE_MAP.get(normalized)
    if legacy:
        where_parts.append("type IN (?, ?)")
        params.extend([normalized, legacy])
    else:
        where_parts.append("type = ?")
        params.append(normalized)


def _belief_level(strength) -> str:
    try:
        val = float(strength or 0)
    except (TypeError, ValueError):
        val = 0.0
    if val >= 0.75:
        return "核心"
    if val >= 0.55:
        return "稳定"
    if val >= 0.35:
        return "候选"
    return "微弱"


def _scope_error(code: str, status: int):
    return jsonify({"error": {"code": code}}), status


def _scope_failure(exc: Exception):
    code = getattr(exc, "reason_code", None) or getattr(exc, "code", None) or "invalid_scope"
    return _scope_error(str(code), 400 if code in {"scope_required", "pagination_required"} else 422)


def _legacy_mutation_disabled(handler):
    """Keep unresolved legacy rows auditable but permanently non-mutable in formal APIs."""
    @wraps(handler)
    async def reject(*args, **kwargs):
        return _scope_error("legacy_mutation_disabled", 410)
    return reject


def _scoped_repo(container):
    repo = getattr(getattr(container, "db", None), "scoped_knowledge", None)
    if repo is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    return repo


def _group_scope_from_query() -> RuntimeScope:
    required = ("bot_id", "session_id", "visibility")
    if any(request.args.get(field) is None for field in required):
        raise ScopedKnowledgeScopeError("scope_required")
    bot_id, session_id, visibility = (request.args.get(field) for field in required)
    if visibility != "group":
        raise ScopedKnowledgeScopeError("derived_scope_visibility_unsupported")
    try:
        platform_id, kind, conversation_id = str(session_id).split(":", 2)
    except ValueError as exc:
        raise ScopeValidationError("invalid_session_id", "session_id must be canonical") from exc
    return RuntimeScope(str(bot_id), visibility, SessionRef(str(session_id), platform_id, kind, conversation_id))


def _scope_from_envelope(body: dict) -> RuntimeScope:
    if "scope" not in body:
        raise ScopedKnowledgeScopeError("scope_required")
    scope = ScopeCodec.from_dict(body["scope"])
    if not isinstance(scope, RuntimeScope) or scope.visibility != "group":
        raise ScopedKnowledgeScopeError("derived_scope_visibility_unsupported")
    return scope


def _page_from_query() -> tuple[int, int]:
    if request.args.get("page") is None or request.args.get("page_size") is None:
        raise ScopedKnowledgeScopeError("pagination_required")
    try:
        page, page_size = int(request.args["page"]), int(request.args["page_size"])
    except (TypeError, ValueError) as exc:
        raise ScopedKnowledgeScopeError("invalid_pagination") from exc
    if page < 1 or page_size < 1 or page_size > 100:
        raise ScopedKnowledgeScopeError("invalid_pagination")
    return page, page_size


def _scoped_page(items: list[dict], *, page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "page": {
            "number": page,
            "page_size": page_size,
            "total": len(items),
            "total_status": "exact",
            "has_next": start + page_size < len(items),
        },
    }


def _find_scoped_belief(repo, scope: RuntimeScope, belief_id: int) -> dict:
    for row in repo.list_scoped_beliefs(scope, limit=10000):
        if row.get("id") == belief_id:
            return row
    raise LookupError("scoped_object_not_found")


def _formal_belief(row: dict) -> dict:
    item = dict(row)
    item["type"] = _normalize_belief_type(item.pop("belief_type", "")) or "world_view"
    item["confidence"] = item.get("strength", 0.0)
    item["level"] = _belief_level(item.get("strength"))
    return item


def _delete_scoped_belief(repo, scope: RuntimeScope, belief_id: int) -> bool:
    """仓储尚未暴露删除端口时，仍经其连接执行严格 Scope 谓词删除。"""
    session_id = scope.session.id if scope.session else ""
    cm = getattr(repo, "cm", None)
    if cm is None:
        raise ScopedKnowledgeScopeError("scoped_repository_unavailable")
    cur = cm.execute_write(
        "DELETE FROM scoped_beliefs WHERE id=? AND bot_id=? AND session_id=? AND visibility=?",
        (belief_id, scope.bot_id, session_id, scope.visibility),
    )
    cm.commit()
    return bool(getattr(cur, "rowcount", 0))


@beliefs_bp.route("/legacy/audit", methods=["GET"])
@require_auth
async def legacy_list_beliefs():
    """旧 beliefs 表的只读审查视图；不属于正式 Scope API。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"items": [], "total": 0, "pending_count": 0})

    status = request.args.get("status")  # pending / active / archived / pending_legacy
    bot_id = request.args.get("bot_id")
    belief_type = request.args.get("type") # self_identity / person_judgment / world_view / preference
    search_q = request.args.get("search") # 搜索内容

    try:
        limit = int(request.args.get("limit") or request.args.get("size") or 50)
    except (ValueError, TypeError):
        limit = 50
    limit = max(1, min(limit, 500))
    try:
        if request.args.get("offset") is not None:
            offset = int(request.args.get("offset", 0))
        elif request.args.get("page") is not None:
            offset = (max(1, int(request.args.get("page", 1))) - 1) * limit
        else:
            offset = 0
    except (ValueError, TypeError):
        offset = 0

    where_parts = ["1=1"]
    params = []
    if status:
        where_parts.append("status = ?")
        params.append(status)
    if bot_id:
        where_parts.append("bot_id = ?")
        params.append(bot_id)
    if belief_type:
        _append_belief_type_filter(where_parts, params, belief_type)
    if search_q:
        where_parts.append("content LIKE ?")
        params.append(f"%{search_q.strip()}%")

    # 排除身份污染 / 清理标记的信念
    reason_excl = ",".join("?" for _ in _EXCLUDED_REASONS)
    where_parts.append(f"COALESCE(archived_reason, '') NOT IN ({reason_excl})")
    params.extend(_EXCLUDED_REASONS)

    where_sql = " AND ".join(where_parts)
    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(beliefs)").fetchall()}
    evidence_type_expr = "evidence_type" if "evidence_type" in cols else "'memory' AS evidence_type"
    evidence_ids_expr = "evidence_ids" if "evidence_ids" in cols else "'[]' AS evidence_ids"
    sql = f"SELECT id, content, type, strength, bot_id, sources, status, created_at, last_reinforced, {evidence_type_expr}, {evidence_ids_expr} FROM beliefs WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()

    # total COUNT 加 WHERE 条件（和列表查询一致）
    count_sql = f"SELECT COUNT(*) FROM beliefs WHERE {where_sql}"
    total = c.db.conn.execute(count_sql, params[:-2]).fetchone()[0]
    pending_count = c.db.conn.execute(
        f"SELECT COUNT(*) FROM beliefs WHERE status = 'pending' AND COALESCE(archived_reason, '') NOT IN ({reason_excl})",
        list(_EXCLUDED_REASONS),
    ).fetchone()[0]

    items = []
    for r in rows:
        sources = json.loads(r[5] or "[]")
        evidence_ids = json.loads(r[10] or "[]")
        evidence_type = r[9] or "memory"
        items.append({
            "id": r[0], "content": r[1], "type": _normalize_belief_type(r[2]) or "world_view", "confidence": r[3],
            "bot_id": r[4], "source": evidence_type, "evidence_type": evidence_type,
            "sources": evidence_ids or sources, "raw_sources": sources, "status": r[6],
            "created_at": r[7], "updated_at": r[8], "level": _belief_level(r[3]),
            "legacy": True, "unresolved_legacy": True, "scope": None,
        })
    return jsonify({
        "items": items, "total": total, "pending_count": pending_count,
        "legacy": True, "unresolved_legacy": True, "scope": None, "readonly": True,
        "page": {"number": offset // limit + 1, "page_size": limit, "total": total,
                 "total_status": "exact", "has_next": offset + len(items) < total},
    })


@beliefs_bp.route("/", methods=["GET"])
@require_auth
async def list_beliefs():
    """正式 scoped beliefs 列表；完整 Scope 和分页均为必填。"""
    try:
        scope = _group_scope_from_query()
        page, page_size = _page_from_query()
        rows = _scoped_repo(get_container()).list_scoped_beliefs(
            scope, status=request.args.get("status"), limit=10000,
        )
        belief_type = _normalize_belief_type(request.args.get("type"))
        search = (request.args.get("search") or "").strip()
        if belief_type:
            rows = [row for row in rows if _normalize_belief_type(row.get("belief_type")) == belief_type]
        if search:
            rows = [row for row in rows if search in str(row.get("content") or "")]
        payload = _scoped_page([_formal_belief(row) for row in rows], page=page, page_size=page_size)
        payload["scope"] = ScopeCodec.to_dict(scope)
        return jsonify(payload)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/", methods=["POST"])
@require_auth
async def create_belief():
    """手动创建 scoped 信念；scope 必须为 ScopeCodec envelope。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        content = body.get("content")
        belief_key = body.get("belief_key")
        belief_type = _normalize_belief_type(body.get("type", "world_view"))
        if belief_type not in _VALID_BELIEF_TYPES:
            raise ScopedKnowledgeScopeError("invalid_belief_type")
        strength = max(0.0, min(1.0, float(body.get("strength", body.get("confidence", 0.5)))))
        belief_id = _scoped_repo(get_container()).upsert_scoped_belief(
            scope, belief_key=belief_key, content=content, belief_type=belief_type,
            strength=strength, status=str(body.get("status") or "pending"),
            source_memory_id=body.get("source_memory_id"), provenance=body.get("provenance") or {},
        )
        return jsonify({"ok": True, "belief_id": belief_id, "scope": ScopeCodec.to_dict(scope)})
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/<int:belief_id>", methods=["PUT"])
@require_auth
async def edit_belief(belief_id: int):
    """编辑一个 scoped 信念；跨 Scope ID 以 404 隐藏。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        current = _find_scoped_belief(repo, scope, belief_id)
        belief_type = _normalize_belief_type(body.get("type", current["belief_type"]))
        if belief_type not in _VALID_BELIEF_TYPES:
            raise ScopedKnowledgeScopeError("invalid_belief_type")
        content = body.get("content", current["content"])
        strength = max(0.0, min(1.0, float(body.get("strength", body.get("confidence", current["strength"])))) )
        updated_id = repo.upsert_scoped_belief(
            scope, belief_key=current["belief_key"], content=content, belief_type=belief_type,
            strength=strength, status=str(body.get("status", current["status"])),
            source_memory_id=body.get("source_memory_id", current.get("source_memory_id")),
            provenance=body.get("provenance", current.get("provenance") or {}),
        )
        return jsonify({"ok": True, "belief_id": updated_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/legacy/<int:belief_id>/evidence", methods=["GET"])
@require_auth
async def legacy_belief_evidence(belief_id: int):
    """旧 belief 的只读证据审查路径。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(beliefs)").fetchall()}
    evidence_type_expr = "evidence_type" if "evidence_type" in cols else "'memory' AS evidence_type"
    evidence_ids_expr = "evidence_ids" if "evidence_ids" in cols else "'[]' AS evidence_ids"
    row = c.db.conn.execute(
        f"SELECT sources, {evidence_type_expr}, {evidence_ids_expr} FROM beliefs WHERE id = ?", (belief_id,)
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "belief not found"}), 404

    raw_sources = json.loads(row[0] or "[]")
    evidence_type = row[1] or "memory"
    evidence_ids = json.loads(row[2] or "[]") or raw_sources
    episodes = []
    memories = []
    relationship_events = []

    def _load_memories(ids: list[int]):
        if not ids or not _table_exists(c.db.conn, "memories"):
            return []
        placeholders = ",".join("?" * len(ids))
        rows = c.db.conn.execute(
            f"SELECT id, content, sender_name, sender_id, timestamp, group_id FROM memories WHERE id IN ({placeholders}) ORDER BY timestamp ASC",
            ids,
        ).fetchall()
        return [
            {"id": r[0], "content": r[1], "sender_name": r[2] or "", "sender_id": r[3] or "", "timestamp": r[4], "group_id": r[5]}
            for r in rows
        ]

    if evidence_ids and evidence_type == "relationship_event" and _table_exists(c.db.conn, "relationship_events"):
        placeholders = ",".join("?" * len(evidence_ids))
        ev_rows = c.db.conn.execute(
            f"""SELECT id, bot_id, group_id, user_id, event_type, dimension, delta, reason,
                       source_episode_id, source_memory_id, created_at
                  FROM relationship_events WHERE id IN ({placeholders}) ORDER BY created_at ASC""",
            evidence_ids,
        ).fetchall()
        memory_ids = []
        episode_ids = []
        for r in ev_rows:
            relationship_events.append({
                "id": r[0], "bot_id": r[1], "group_id": r[2], "user_id": r[3],
                "event_type": r[4], "dimension": r[5], "delta": r[6], "reason": r[7],
                "source_episode_id": r[8], "source_memory_id": r[9], "created_at": r[10],
            })
            if r[8]:
                episode_ids.append(int(r[8]))
            if r[9]:
                memory_ids.append(int(r[9]))
        memories = _load_memories(list(dict.fromkeys(memory_ids)))
        if episode_ids:
            evidence_ids = list(dict.fromkeys(episode_ids))
            evidence_type = "episode"

    if evidence_ids and evidence_type in {"episode", "experience_episode"} and _table_exists(c.db.conn, "experience_episodes"):
        placeholders = ",".join("?" * len(evidence_ids))
        ep_rows = c.db.conn.execute(
            f"SELECT id, trigger_text, outcome, bot_inner_thought, bot_reply, source_memory_ids, created_at, episode_type FROM experience_episodes WHERE id IN ({placeholders}) ORDER BY created_at ASC",
            evidence_ids,
        ).fetchall()
        all_memory_ids = set()
        for r in ep_rows:
            try:
                mem_ids = json.loads(r[5] or "[]")
                if isinstance(mem_ids, list):
                    all_memory_ids.update(int(x) for x in mem_ids if x)
            except Exception:
                pass
            episodes.append({
                "id": r[0], "trigger": r[1] or "", "outcome": r[2] or "",
                "bot_inner_thought": r[3] or "", "bot_reply": r[4] or "",
                "created_at": r[6], "episode_type": r[7] or "",
            })
        memories = memories or _load_memories(list(all_memory_ids))

    if raw_sources and not memories and evidence_type == "memory":
        memories = _load_memories(raw_sources)

    return jsonify({
        "ok": True,
        "belief_id": belief_id,
        "sources": evidence_ids,
        "raw_sources": raw_sources,
        "evidence_type": evidence_type,
        "episodes": episodes,
        "relationship_events": relationship_events,
        "memories": memories,
        "items": memories,
        "legacy": True,
        "unresolved_legacy": True,
        "scope": None,
        "readonly": True,
    })


@beliefs_bp.route("/<int:belief_id>/approve", methods=["POST"])
@require_auth
async def approve_belief(belief_id: int):
    """审核通过 scoped belief；证据只接受同 Scope 的 source_memory_id。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        current = _find_scoped_belief(repo, scope, belief_id)
        if not current.get("source_memory_id"):
            return jsonify({"ok": False, "error": "Cannot approve belief without scoped source_memory_id"}), 400
        repo.upsert_scoped_belief(
            scope, belief_key=current["belief_key"], content=current["content"],
            belief_type=current["belief_type"], strength=current["strength"], status="active",
            source_memory_id=current["source_memory_id"], provenance=current.get("provenance") or {},
        )
        return jsonify({"ok": True, "belief_id": belief_id, "new_status": "active", "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/<int:belief_id>/archive", methods=["POST"])
@require_auth
async def archive_belief(belief_id: int):
    """归档 scoped 信念。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        current = _find_scoped_belief(repo, scope, belief_id)
        repo.upsert_scoped_belief(
            scope, belief_key=current["belief_key"], content=current["content"],
            belief_type=current["belief_type"], strength=current["strength"], status="archived",
            source_memory_id=current.get("source_memory_id"), provenance=current.get("provenance") or {},
        )
        return jsonify({"ok": True, "belief_id": belief_id, "new_status": "archived", "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/<int:belief_id>", methods=["DELETE"])
@require_auth
async def delete_belief(belief_id: int):
    """删除 scoped 信念；不会触及 legacy beliefs 表。"""
    body = await request.get_json(silent=True) or {}
    try:
        scope = _scope_from_envelope(body)
        repo = _scoped_repo(get_container())
        _find_scoped_belief(repo, scope, belief_id)
        _delete_scoped_belief(repo, scope, belief_id)
        return jsonify({"ok": True, "deleted": belief_id, "scope": ScopeCodec.to_dict(scope)})
    except LookupError:
        return _scope_error("scoped_object_not_found", 404)
    except (ScopeValidationError, ScopedKnowledgeScopeError, TypeError, ValueError) as exc:
        return _scope_failure(exc)


@beliefs_bp.route("/batch-archive", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_archive():
    """批量归档旧信念（v1.1.0 #2.2）。
    body 可选: {"before_ts": 1718000000} 不传则归档所有 active 信念。
    """
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500
    body = await request.get_json(force=True, silent=True) or {}
    before_ts = body.get("before_ts")
    if before_ts:
        cur = c.db.conn.execute(
            "UPDATE beliefs SET status = 'archived', archived_reason = 'batch_archive' WHERE status = 'active' AND created_at < ?",
            (int(before_ts),),
        )
    else:
        cur = c.db.conn.execute(
            "UPDATE beliefs SET status = 'archived', archived_reason = 'batch_archive' WHERE status = 'active'",
        )
    c.db.conn.commit()
    return jsonify({"ok": True, "archived_count": cur.rowcount})


@beliefs_bp.route("/batch-archive-selected", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_archive_selected_beliefs():
    """批量归档前端已勾选的信念。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500
    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    now = int(time.time())
    if all_matching:
        status = body.get("status")
        bot_id = body.get("bot_id")
        belief_type = body.get("type")
        search_q = body.get("search")
        where_parts = ["1=1"]
        params = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if bot_id:
            where_parts.append("bot_id = ?")
            params.append(bot_id)
        if belief_type:
            _append_belief_type_filter(where_parts, params, belief_type)
        if search_q:
            where_parts.append("content LIKE ?")
            params.append(f"%{search_q.strip()}%")
        reason_excl = ",".join("?" for _ in _EXCLUDED_REASONS)
        where_parts.append(f"COALESCE(archived_reason, '') NOT IN ({reason_excl})")
        params.extend(_EXCLUDED_REASONS)
        where_sql = " AND ".join(where_parts)
        cur = c.db.conn.execute(
            f"UPDATE beliefs SET status = 'archived', archived_reason = 'webui_batch_archive', last_reinforced = ? WHERE {where_sql}",
            [now] + params,
        )
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
        placeholders = ",".join("?" * len(ids))
        cur = c.db.conn.execute(
            f"UPDATE beliefs SET status = 'archived', archived_reason = 'webui_batch_archive', last_reinforced = ? WHERE id IN ({placeholders})",
            [now] + ids,
        )
    c.db.conn.commit()
    return jsonify({"ok": True, "archived_count": cur.rowcount})


@beliefs_bp.route("/batch-approve", methods=["POST"])
@require_auth
@_legacy_mutation_disabled
async def batch_approve_beliefs():
    """批量审核通过信念（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    
    now = int(time.time())
    approved_count = 0
    skipped_ids = []
    
    if all_matching:
        # 跨页全选模式：读取前端发过来的过滤条件
        status = body.get("status")
        bot_id = body.get("bot_id")
        belief_type = body.get("type")
        search_q = body.get("search")
        
        where_parts = ["status IN ('pending','challenged','pending_legacy')"]
        params = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if bot_id:
            where_parts.append("bot_id = ?")
            params.append(bot_id)
        if belief_type:
            _append_belief_type_filter(where_parts, params, belief_type)
        if search_q:
            where_parts.append("content LIKE ?")
            params.append(f"%{search_q.strip()}%")
            
        reason_excl = ",".join("?" for _ in _EXCLUDED_REASONS)
        where_parts.append(f"COALESCE(archived_reason, '') NOT IN ({reason_excl})")
        params.extend(_EXCLUDED_REASONS)
        
        where_sql = " AND ".join(where_parts)
        rows = c.db.conn.execute(
            f"SELECT id, sources FROM beliefs WHERE {where_sql}", params
        ).fetchall()
    else:
        # 普通勾选模式
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        rows = c.db.conn.execute(
            f"SELECT id, sources FROM beliefs WHERE id IN ({placeholders})", ids
        ).fetchall()

    for r in rows:
        bid = r[0]
        sources = json.loads(r[1] or "[]")
        # 强制搭配：必须要有证据
        if not sources:
            skipped_ids.append(bid)
            continue
            
        c.db.conn.execute(
            "UPDATE beliefs SET status = 'active', last_reinforced = ? WHERE id = ? AND status IN ('pending','challenged','pending_legacy')",
            (now, bid),
        )
        approved_count += 1
        
    c.db.conn.commit()
    return jsonify({
        "ok": True, 
        "approved_count": approved_count, 
        "skipped_count": len(skipped_ids),
        "skipped_ids": skipped_ids
    })


@beliefs_bp.route("/batch-delete", methods=["POST"])
@require_auth
async def batch_delete_beliefs():
    """批量删除信念（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    
    if all_matching:
        status = body.get("status")
        bot_id = body.get("bot_id")
        belief_type = body.get("type")
        search_q = body.get("search")
        
        where_parts = ["1=1"]
        params = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if bot_id:
            where_parts.append("bot_id = ?")
            params.append(bot_id)
        if belief_type:
            _append_belief_type_filter(where_parts, params, belief_type)
        if search_q:
            where_parts.append("content LIKE ?")
            params.append(f"%{search_q.strip()}%")
            
        reason_excl = ",".join("?" for _ in _EXCLUDED_REASONS)
        where_parts.append(f"COALESCE(archived_reason, '') NOT IN ({reason_excl})")
        params.extend(_EXCLUDED_REASONS)
        
        where_sql = " AND ".join(where_parts)
        cur = c.db.conn.execute(f"DELETE FROM beliefs WHERE {where_sql}", params)
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        cur = c.db.conn.execute(f"DELETE FROM beliefs WHERE id IN ({placeholders})", ids)
        
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted_count": cur.rowcount})
