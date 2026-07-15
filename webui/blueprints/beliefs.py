"""Beliefs Blueprint — 信念管理 CRUD + approve/archive (US-1.2)"""

from __future__ import annotations

import json
import time

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

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


@beliefs_bp.route("/", methods=["GET"])
@require_auth
async def list_beliefs():
    """列出信念（支持 status / bot_id / type 筛选，支持内容搜索）。"""
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
        })
    return jsonify({"items": items, "total": total, "pending_count": pending_count})


@beliefs_bp.route("/", methods=["POST"])
@require_auth
async def create_belief():
    """手动创建信念。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    body = await request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content or len(content) < 5:
        return jsonify({"ok": False, "error": "content required (min 5 chars)"}), 400

    belief_type = _normalize_belief_type(body.get("type", "world_view"))
    if belief_type not in _VALID_BELIEF_TYPES:
        belief_type = "world_view"
    try:
        strength = float(body.get("strength", body.get("confidence", 0.5)))
    except (ValueError, TypeError):
        strength = 0.5
    strength = max(0.0, min(1.0, strength))
    bot_id = body.get("bot_id", "bot")
    sources = body.get("sources") or []
    if not isinstance(sources, list):
        sources = []

    try:
        belief_id = c.db.add_belief(
            content=content,
            belief_type=belief_type,
            bot_id=bot_id,
            strength=strength,
            sources=sources[:20],
            status="pending",  # 手动创建也进入待审
        )
        return jsonify({"ok": True, "belief_id": belief_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@beliefs_bp.route("/<int:belief_id>", methods=["PUT"])
@require_auth
async def edit_belief(belief_id: int):
    """编辑信念 content/strength/type。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    body = await request.get_json(silent=True) or {}
    sets = []
    params = []
    if "content" in body and body["content"]:
        sets.append("content = ?")
        params.append(str(body["content"]).strip())
    strength_value = body.get("strength", body.get("confidence"))
    if strength_value is not None:
        try:
            sets.append("strength = ?")
            params.append(max(0.0, min(1.0, float(strength_value))))
        except (ValueError, TypeError):
            pass
    if "type" in body and body["type"]:
        t = _normalize_belief_type(body["type"])
        if t in _VALID_BELIEF_TYPES:
            sets.append("type = ?")
            params.append(t)
    if not sets:
        return jsonify({"ok": False, "error": "No valid fields to update"}), 400
    sets.append("last_reinforced = ?")
    params.append(int(time.time()))
    params.append(belief_id)
    c.db.conn.execute(f"UPDATE beliefs SET {', '.join(sets)} WHERE id = ?", params)
    c.db.conn.commit()
    return jsonify({"ok": True, "belief_id": belief_id})


@beliefs_bp.route("/<int:belief_id>/evidence", methods=["GET"])
@require_auth
async def belief_evidence(belief_id: int):
    """返回 sources/evidence_ids 关联的 memories / episodes / relationship_events。"""
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
    })


@beliefs_bp.route("/<int:belief_id>/approve", methods=["POST"])
@require_auth
async def approve_belief(belief_id: int):
    """审核通过：pending → active。检查 evidence（sources 不能为空）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500

    # 检查是否有 evidence
    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(beliefs)").fetchall()}
    evidence_ids_expr = "evidence_ids" if "evidence_ids" in cols else "'[]' AS evidence_ids"
    row = c.db.conn.execute(
        f"SELECT sources, {evidence_ids_expr} FROM beliefs WHERE id = ?", (belief_id,)
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "belief not found"}), 404

    sources = json.loads(row[0] or "[]")
    evidence_ids = json.loads(row[1] or "[]")
    if not (sources or evidence_ids):
        return jsonify({"ok": False, "error": "Cannot approve belief without evidence (sources/evidence_ids is empty)"}), 400

    c.db.conn.execute(
        "UPDATE beliefs SET status = 'active', last_reinforced = ? WHERE id = ? AND status IN ('pending','challenged','pending_legacy')",
        (int(time.time()), belief_id),
    )
    c.db.conn.commit()
    return jsonify({"ok": True, "belief_id": belief_id, "new_status": "active"})


@beliefs_bp.route("/<int:belief_id>/archive", methods=["POST"])
@require_auth
async def archive_belief(belief_id: int):
    """归档信念。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500
    c.db.conn.execute(
        "UPDATE beliefs SET status = 'archived', archived_reason = ? WHERE id = ?",
        ("webui_manual", belief_id),
    )
    c.db.conn.commit()
    return jsonify({"ok": True, "belief_id": belief_id, "new_status": "archived"})


@beliefs_bp.route("/<int:belief_id>", methods=["DELETE"])
@require_auth
async def delete_belief(belief_id: int):
    """删除信念。"""
    c = get_container()
    if not _table_exists(c.db.conn, "beliefs"):
        return jsonify({"ok": False, "error": "beliefs table not found"}), 500
    c.db.conn.execute("DELETE FROM beliefs WHERE id = ?", (belief_id,))
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted": belief_id})


@beliefs_bp.route("/batch-archive", methods=["POST"])
@require_auth
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
