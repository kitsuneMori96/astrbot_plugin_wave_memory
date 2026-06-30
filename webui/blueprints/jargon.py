"""Jargon Blueprint — 黑话管理 WebUI API (US-4.4)"""

from __future__ import annotations

import hashlib
import json
import time

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...services.jargon.holyman_assets import content_entries
except ImportError:  # test/runtime fallback when plugin root is on sys.path
    from services.jargon.holyman_assets import content_entries

jargon_bp = Blueprint("jargon", __name__, url_prefix="/api/jargon")


HOLYMAN_CATEGORY_LABELS = {
    "skill-core": "核心技能",
    "gaming": "游戏文化",
    "internet-culture": "互联网文化",
    "communication": "沟通风格",
    "rules": "人格规则",
    "values": "价值观",
    "iconic-quotes": "标志语录",
    "internal-quotes": "内部语录",
    "corpus": "神言语料",
    "legacy": "旧版内置",
    "unknown": "未分类",
}


def _normalize_holyman_phrase(word: str, value) -> dict:
    """兼容旧版字符串 value 和新版 Holyman 结构化 value。"""
    if isinstance(value, dict):
        category = str(value.get("category") or "unknown")
        meaning = str(value.get("meaning") or value.get("explanation") or "")
        source = str(value.get("source") or "")
        kind = str(value.get("kind") or "phrase")
    else:
        category = "legacy"
        meaning = str(value or "")
        source = ""
        kind = "legacy"
    return {
        "word": word,
        "meaning": meaning,
        "category": category,
        "category_label": HOLYMAN_CATEGORY_LABELS.get(category, category),
        "source": source,
        "kind": kind,
    }


def _build_holyman_categories(items: list[dict]) -> list[dict]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for item in items:
        category = item.get("category") or "unknown"
        counts[category] = counts.get(category, 0) + 1
        labels[category] = item.get("category_label") or HOLYMAN_CATEGORY_LABELS.get(category, category)
    return [
        {"id": category, "label": labels[category], "count": count}
        for category, count in sorted(counts.items(), key=lambda kv: (-kv[1], labels.get(kv[0], kv[0])))
    ]


def _holyman_content_entries(phrases: dict) -> dict:
    if not isinstance(phrases, dict):
        return {}
    return {
        key: value
        for key, value in phrases.items()
        if isinstance(key, str) and not key.startswith("_")
    }


def _holyman_content_hash(phrases: dict) -> str:
    payload = json.dumps(_holyman_content_entries(phrases), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _holyman_content_status(phrases: dict, local_count: int, remote_version: str, quality_report: dict | None = None) -> dict:
    """Judge Holyman asset health by quality report, not by phrase count alone."""
    quality_report = quality_report or {}
    content_hash = str(phrases.get("_content_hash") or _holyman_content_hash(phrases))
    content_count = _safe_int(phrases.get("_content_count"), local_count)
    remote_commit_version = phrases.get("_remote_commit_version") or phrases.get("_remote_version") or ""
    has_quality_report = bool(quality_report)
    report_status = str(quality_report.get("status") or "")
    if has_quality_report:
        asset_status = report_status
    elif content_count >= 300 and content_hash:
        # Compatibility for old tests/assets that already carry content-derived metadata.
        asset_status = "ready"
    else:
        asset_status = "legacy"
    is_update_available = asset_status != "ready"

    if asset_status == "ready" and remote_version != "Unknown" and remote_commit_version:
        is_update_available = not content_hash

    return {
        "content_hash": content_hash,
        "content_count": content_count,
        "remote_commit_version": remote_commit_version,
        "is_update_available": is_update_available,
        "asset_status": asset_status,
    }


def _table_exists(conn, table: str) -> bool:
    """检查表是否存在。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _safe_int(val, default):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_json_list(val):
    try:
        parsed = json.loads(val or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _clamp_int(val, default, min_value=0, max_value=50):
    num = _safe_int(val, default)
    return max(min_value, min(max_value, num))


@jargon_bp.route("/", methods=["GET"])
@require_auth
async def list_jargon():
    """列出黑话（支持 group_id / status 筛选，支持内容搜索）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"items": [], "total": 0})
    group_id = request.args.get("group_id")
    status = request.args.get("status")  # confirmed / pending / rejected
    search_q = request.args.get("search")  # 搜索词条名或释义

    limit = _safe_int(request.args.get("limit", 50), 50)
    offset = _safe_int(request.args.get("offset", 0), 0)

    where_parts = ["1=1"]
    params = []
    if group_id:
        where_parts.append("group_id = ?")
        params.append(group_id)
    # 改用 status 字段筛选（COALESCE 处理 NULL → 'pending'）
    if status:
        where_parts.append("COALESCE(status, 'pending') = ?")
        params.append(status)
    if search_q:
        where_parts.append("(word LIKE ? OR meaning LIKE ?)")
        sq = f"%{search_q.strip()}%"
        params.extend([sq, sq])

    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(jargon)").fetchall()}
    extra_cols = [
        "source_memory_id" if "source_memory_id" in cols else "NULL AS source_memory_id",
        "source_message_ts" if "source_message_ts" in cols else "NULL AS source_message_ts",
        "source_sender_id" if "source_sender_id" in cols else "NULL AS source_sender_id",
        "source_context" if "source_context" in cols else "'[]' AS source_context",
        "candidate_type" if "candidate_type" in cols else "'jargon' AS candidate_type",
        "reject_reason" if "reject_reason" in cols else "NULL AS reject_reason",
        "status" if "status" in cols else "'pending' AS status",
    ]
    where_sql = " AND ".join(where_parts)
    sql = f"""SELECT id, word, meaning, is_jargon, frequency, confidence, is_global, group_id,
              contexts, created_at, {', '.join(extra_cols)}
              FROM jargon WHERE {where_sql} ORDER BY frequency DESC LIMIT ? OFFSET ?"""
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    # total COUNT 加 WHERE 条件（和列表查询一致）
    count_sql = f"SELECT COUNT(*) FROM jargon WHERE {where_sql}"
    total = c.db.conn.execute(count_sql, params[:-2]).fetchone()[0]
    items = [
        {"id": r[0], "word": r[1], "meaning": r[2], "is_jargon": r[3],
         "frequency": r[4], "confidence": r[5], "is_global": bool(r[6]),
         "group_id": r[7], "contexts": _safe_json_list(r[8]), "created_at": r[9],
         "source_memory_id": r[10], "source_message_ts": r[11], "source_sender_id": r[12],
         "source_context": _safe_json_list(r[13]), "candidate_type": r[14] or "jargon",
         "reject_reason": r[15], "status": r[16] or "pending"}
        for r in rows
    ]
    pending_count = c.db.conn.execute(
        "SELECT COUNT(*) FROM jargon WHERE COALESCE(status, 'pending') = 'pending'"
    ).fetchone()[0]
    return jsonify({"items": items, "total": total, "pending_count": pending_count})


@jargon_bp.route("/", methods=["POST"])
@require_auth
async def create_jargon():
    """手动创建黑话词条。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500

    body = await request.get_json() or {}
    word = body.get("word", "").strip()
    meaning = body.get("meaning", "").strip()
    group_id = body.get("group_id")
    if group_id:
        group_id = str(group_id).strip()

    if not word:
        return jsonify({"ok": False, "error": "Word is required"}), 400

    # 检查是否已存在
    dup = c.db.conn.execute("SELECT id FROM jargon WHERE word = ? AND (group_id = ? OR (group_id IS NULL AND ? IS NULL))", (word, group_id, group_id)).fetchone()
    if dup:
        return jsonify({"ok": False, "error": f"Jargon '{word}' already exists"}), 400

    now = int(time.time())
    is_global = 1 if not group_id else 0
    c.db.conn.execute(
        "INSERT INTO jargon (word, meaning, is_jargon, status, frequency, confidence, is_global, group_id, contexts, created_at, updated_at) VALUES (?, ?, 1, 'confirmed', 1, 1.0, ?, ?, '[]', ?, ?)",
        (word, meaning, is_global, group_id, now, now)
    )
    c.db.conn.commit()
    new_id = c.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok": True, "id": new_id})


@jargon_bp.route("/<int:jargon_id>/context", methods=["GET"])
@require_auth
async def get_jargon_context(jargon_id: int):
    """按黑话锚点动态截取原始聊天上下文。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    before = _clamp_int(request.args.get("before"), 5)
    after = _clamp_int(request.args.get("after"), 5)

    cols = {r[1] for r in c.db.conn.execute("PRAGMA table_info(jargon)").fetchall()}
    extra_cols = [
        "source_memory_id" if "source_memory_id" in cols else "NULL AS source_memory_id",
        "source_message_ts" if "source_message_ts" in cols else "NULL AS source_message_ts",
        "source_sender_id" if "source_sender_id" in cols else "NULL AS source_sender_id",
        "source_context" if "source_context" in cols else "'[]' AS source_context",
        "candidate_type" if "candidate_type" in cols else "'jargon' AS candidate_type",
    ]
    row = c.db.conn.execute(
        f"""SELECT id, word, meaning, group_id, contexts, {', '.join(extra_cols)}
            FROM jargon WHERE id = ?""",
        (jargon_id,),
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "jargon not found"}), 404

    jargon = {
        "id": row[0], "word": row[1], "meaning": row[2], "group_id": row[3],
        "contexts": _safe_json_list(row[4]), "source_memory_id": row[5],
        "source_message_ts": row[6], "source_sender_id": row[7],
        "source_context": _safe_json_list(row[8]), "candidate_type": row[9] or "jargon",
    }

    anchor = None
    if jargon["source_memory_id"]:
        anchor = c.db.conn.execute(
            """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
               WHERE id = ?""",
            (jargon["source_memory_id"],),
        ).fetchone()

    if not anchor and jargon["source_message_ts"]:
        word_like = f"%{jargon['word']}%"
        sender_id = str(jargon.get("source_sender_id") or "")
        anchor = c.db.conn.execute(
            """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
               WHERE group_id = ? AND timestamp BETWEEN ? AND ?
                 AND (? = '' OR sender_id = ?)
                 AND content LIKE ?
               ORDER BY ABS(timestamp - ?) ASC LIMIT 1""",
            (jargon["group_id"], float(jargon["source_message_ts"]) - 60, float(jargon["source_message_ts"]) + 60,
             sender_id, sender_id, word_like, float(jargon["source_message_ts"])),
        ).fetchone()
        if anchor and "source_memory_id" in cols:
            c.db.conn.execute("UPDATE jargon SET source_memory_id = ? WHERE id = ?", (anchor[0], jargon_id))
            c.db.conn.commit()
            jargon["source_memory_id"] = anchor[0]

    def _row_to_msg(r, role):
        return {
            "id": r[0], "group_id": r[1], "sender_id": r[2], "sender_name": r[3],
            "content": r[4], "timestamp": r[5], "role": role,
        }

    fallback_contexts = jargon["source_context"] or jargon["contexts"]
    if not anchor:
        return jsonify({
            "ok": True,
            "jargon": jargon,
            "anchor": None,
            "messages": [],
            "fallback_contexts": fallback_contexts,
            "used_fallback": True,
        })

    anchor_msg = _row_to_msg(anchor, "anchor")
    anchor_ts = float(anchor[5])
    group_id = anchor[1]
    before_rows = c.db.conn.execute(
        """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
           WHERE group_id = ? AND timestamp < ? AND memory_type = 'message'
           ORDER BY timestamp DESC LIMIT ?""",
        (group_id, anchor_ts, before),
    ).fetchall()
    after_rows = c.db.conn.execute(
        """SELECT id, group_id, sender_id, sender_name, content, timestamp FROM memories
           WHERE group_id = ? AND timestamp > ? AND memory_type = 'message'
           ORDER BY timestamp ASC LIMIT ?""",
        (group_id, anchor_ts, after),
    ).fetchall()
    messages = [_row_to_msg(r, "before") for r in reversed(before_rows)] + [anchor_msg] + [_row_to_msg(r, "after") for r in after_rows]
    return jsonify({
        "ok": True,
        "jargon": jargon,
        "anchor": anchor_msg,
        "messages": messages,
        "fallback_contexts": fallback_contexts,
        "used_fallback": False,
    })


@jargon_bp.route("/holyman/candidates", methods=["GET"])
@require_auth
async def list_holyman_candidates():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"items": []})
    rows = c.db.conn.execute("SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates ORDER BY count DESC, word ASC").fetchall()
    return jsonify({"items": [{"id": r[0], "word": r[1], "reason": r[2], "count": r[3], "source": r[4], "status": r[5], "reject_reason": r[6]} for r in rows]})


@jargon_bp.route("/holyman/candidates/<int:candidate_id>/<action>", methods=["POST"])
@require_auth
async def review_holyman_candidate(candidate_id: int, action: str):
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"ok": False, "error": "jargon_candidates table not found"}), 500
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400
    result = _review_holyman_candidate_ids(c.db.conn, [candidate_id], action)
    if result.get("missing_ids"):
        return jsonify({"ok": False, "error": "candidate not found"}), 404
    return jsonify({"ok": True, "candidate_id": candidate_id, "action": action})


@jargon_bp.route("/holyman/candidates/batch-review", methods=["POST"])
@require_auth
async def batch_review_holyman_candidates():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_candidates"):
        return jsonify({"ok": False, "error": "jargon_candidates table not found"}), 500
    body = await request.get_json() or {}
    ids = body.get("ids", [])
    action = str(body.get("action", "approve"))
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "action must be approve or reject"}), 400
    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "error": "ids list is required"}), 400
    result = _review_holyman_candidate_ids(c.db.conn, ids, action)
    return jsonify({"ok": True, "reviewed_count": result["reviewed_count"], "action": action, "blocked_count": result["blocked_count"]})


def _review_holyman_candidate_ids(conn, ids, action: str) -> dict:
    normalized_ids = []
    for candidate_id in ids:
        try:
            normalized_ids.append(int(candidate_id))
        except (TypeError, ValueError):
            continue
    if not normalized_ids:
        return {"reviewed_count": 0, "blocked_count": 0, "missing_ids": []}
    placeholders = ",".join("?" * len(normalized_ids))
    rows = conn.execute(
        f"SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates WHERE id IN ({placeholders})",
        normalized_ids,
    ).fetchall()
    found_ids = {int(r[0]) for r in rows}
    missing_ids = [candidate_id for candidate_id in normalized_ids if candidate_id not in found_ids]
    if missing_ids:
        return {"reviewed_count": 0, "blocked_count": 0, "missing_ids": missing_ids}

    now = int(time.time())
    if action == "approve":
        cur = conn.execute(
            f"UPDATE jargon_candidates SET status = 'approved', updated_at = ? WHERE id IN ({placeholders})",
            [now] + normalized_ids,
        )
        blocked_count = 0
    else:
        cur = conn.execute(
            f"UPDATE jargon_candidates SET status = 'rejected', reject_reason = 'manual_reject', updated_at = ? WHERE id IN ({placeholders})",
            [now] + normalized_ids,
        )
        blocked_count = 0
        for row in rows:
            conn.execute(
                "INSERT OR IGNORE INTO jargon_blocklist (word, reason, source, created_at) VALUES (?, ?, ?, ?)",
                (row[1], "manual_reject", "holyman_review", now),
            )
            blocked_count += 1
    conn.commit()
    return {"reviewed_count": getattr(cur, "rowcount", len(normalized_ids)), "blocked_count": blocked_count, "missing_ids": []}


@jargon_bp.route("/holyman/blocklist", methods=["GET", "POST"])
@require_auth
async def holyman_blocklist():
    c = get_container()
    if not _table_exists(c.db.conn, "jargon_blocklist"):
        return jsonify({"items": []})
    if getattr(request, "method", "GET") == "GET":
        rows = c.db.conn.execute("SELECT id, word, reason, source, created_at FROM jargon_blocklist ORDER BY created_at DESC, word ASC").fetchall()
        return jsonify({"items": [{"id": r[0], "word": r[1], "reason": r[2], "source": r[3], "created_at": r[4]} for r in rows]})
    body = await request.get_json(silent=True) or {}
    word = str(body.get("word", "")).strip()
    reason = str(body.get("reason", "manual_block") or "manual_block")
    if not word:
        return jsonify({"ok": False, "error": "word is required"}), 400
    now = int(time.time())
    c.db.conn.execute("INSERT OR REPLACE INTO jargon_blocklist (word, reason, source, created_at) VALUES (?, ?, ?, ?)", (word, reason, 'holyman_review', now))
    c.db.conn.commit()
    return jsonify({"ok": True, "word": word})


@jargon_bp.route("/<int:jargon_id>", methods=["PUT"])

@require_auth
async def edit_jargon(jargon_id: int):
    """编辑黑话词条/释义。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    body = await request.get_json(silent=True) or {}
    sets = []
    params = []
    if "word" in body:
        sets.append("word = ?")
        params.append(body["word"])
    if "meaning" in body:
        sets.append("meaning = ?")
        params.append(body["meaning"])
    if not sets:
        return jsonify({"error": "Nothing to update"}), 400
    sets.append("updated_at = ?")
    params.append(int(time.time()))
    params.append(jargon_id)
    try:
        c.db.conn.execute(f"UPDATE jargon SET {', '.join(sets)} WHERE id = ?", params)
        c.db.conn.commit()
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return jsonify({"error": "该群已存在同名词条，请使用其他名称"}), 409
        raise
    return jsonify({"ok": True, "jargon_id": jargon_id})


@jargon_bp.route("/<int:jargon_id>", methods=["DELETE"])
@require_auth
async def delete_jargon(jargon_id: int):
    """删除黑话。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    c.db.conn.execute("DELETE FROM jargon WHERE id = ?", (jargon_id,))
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted": jargon_id})


@jargon_bp.route("/<int:jargon_id>/toggle_global", methods=["POST"])
@require_auth
async def toggle_global(jargon_id: int):
    """切换全局状态。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
    row = c.db.conn.execute("SELECT is_global FROM jargon WHERE id = ?", (jargon_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    new_val = 0 if row[0] else 1
    c.db.conn.execute("UPDATE jargon SET is_global = ?, updated_at = ? WHERE id = ?", (new_val, int(time.time()), jargon_id))
    c.db.conn.commit()
    return jsonify({"ok": True, "jargon_id": jargon_id, "is_global": bool(new_val)})


@jargon_bp.route("/batch-review", methods=["POST"])
@require_auth
async def batch_review_jargon():
    """批量审核确认/否决黑话词条（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    action = body.get("action", "approve")  # approve 或 reject
    if action not in {"approve", "reject"}:
        return jsonify({"ok": False, "error": "invalid action"}), 400

    now = int(time.time())
    
    if all_matching:
        group_id = body.get("group_id")
        status = body.get("status")
        search_q = body.get("search")
        
        where_parts = ["1=1"]
        params = []
        if group_id:
            where_parts.append("group_id = ?")
            params.append(group_id)
        if status:
            where_parts.append("COALESCE(status, 'pending') = ?")
            params.append(status)
        if search_q:
            where_parts.append("(word LIKE ? OR meaning LIKE ?)")
            sq = f"%{search_q.strip()}%"
            params.extend([sq, sq])
            
        where_sql = " AND ".join(where_parts)
        if action == "approve":
            cur = c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 1, status = 'confirmed', updated_at = ? WHERE {where_sql}",
                [now] + params,
            )
        else:
            cur = c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 0, status = 'rejected', reject_reason = 'webui_batch_rejected', updated_at = ? WHERE {where_sql}",
                [now] + params,
            )
        reviewed_count = cur.rowcount
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        if action == "approve":
            c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 1, status = 'confirmed', updated_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
        else:
            c.db.conn.execute(
                f"UPDATE jargon SET is_jargon = 0, status = 'rejected', reject_reason = 'webui_batch_rejected', updated_at = ? WHERE id IN ({placeholders})",
                [now] + ids,
            )
        reviewed_count = len(ids)

    c.db.conn.commit()
    return jsonify({"ok": True, "reviewed_count": reviewed_count, "action": action})


@jargon_bp.route("/batch-delete", methods=["POST"])
@require_auth
async def batch_delete_jargon():
    """批量删除黑话词条（支持 all_matching 跨页全选）。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500

    body = await request.get_json() or {}
    all_matching = body.get("all_matching", False)
    
    if all_matching:
        group_id = body.get("group_id")
        status = body.get("status")
        search_q = body.get("search")
        
        where_parts = ["1=1"]
        params = []
        if group_id:
            where_parts.append("group_id = ?")
            params.append(group_id)
        if status:
            where_parts.append("COALESCE(status, 'pending') = ?")
            params.append(status)
        if search_q:
            where_parts.append("(word LIKE ? OR meaning LIKE ?)")
            sq = f"%{search_q.strip()}%"
            params.extend([sq, sq])
            
        where_sql = " AND ".join(where_parts)
        cur = c.db.conn.execute(f"DELETE FROM jargon WHERE {where_sql}", params)
    else:
        ids = body.get("ids", [])
        if not isinstance(ids, list) or not ids:
            return jsonify({"ok": False, "error": "ids list or all_matching is required"}), 400
            
        placeholders = ",".join("?" * len(ids))
        cur = c.db.conn.execute(f"DELETE FROM jargon WHERE id IN ({placeholders})", ids)
        
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted_count": cur.rowcount})


@jargon_bp.route("/holyman", methods=["GET"])
@require_auth
async def get_holyman():
    """获取 Holyman 预设黑话及其数据库激活状态。"""
    c = get_container()
    from pathlib import Path
    import os
    
    # 1. 采用绝对物理隔离寻址，解决在装饰器、闭包或符号链接下 __file__ 盘符漂移的痛点
    local_dir = None
    for target_path in [
        "/AstrBot/data/plugins/astrbot_plugin_wave_memory/assets/holyman",
        os.path.join(os.getcwd(), "data/plugins/astrbot_plugin_wave_memory/assets/holyman"),
        os.path.join(os.getcwd(), "astrbot_plugin_wave_memory/assets/holyman"),
        # 动态自愈 fallback：通过当前 jargon.py 真实的绝对路径计算
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../assets/holyman"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../assets/holyman")
    ]:
        if Path(target_path).exists() and (Path(target_path) / "phrases.json").exists():
            local_dir = Path(target_path)
            break
            
    if not local_dir:
        # 最后的保底，直接使用相对路径，但不使用可能会导致问题的 resolve() 符号解析
        local_dir = Path(os.path.dirname(os.path.abspath(__file__))) / ".." / ".." / "assets" / "holyman"

    phrases_file = local_dir / "phrases.json"

    def _load_asset_json(name, default):
        path = local_dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    phrases = _load_asset_json("phrases.json", {})
    concepts = _load_asset_json("concepts.json", [])
    examples = _load_asset_json("examples.json", [])
    corpus = _load_asset_json("corpus.json", [])
    candidates = _load_asset_json("candidates.json", [])
    blocked = _load_asset_json("blocked.json", {})
    quality_report = _load_asset_json("quality_report.json", {})
    if not isinstance(candidates, list):
        candidates = []
    if not isinstance(blocked, dict):
        blocked = {}

    # DB 审核状态优先覆盖资产快照，保证 approve/reject 后刷新页面立即可见。
    if c.db and hasattr(c.db, "conn") and c.db.conn:
        try:
            if _table_exists(c.db.conn, "jargon_candidates"):
                rows = c.db.conn.execute(
                    "SELECT id, word, reason, count, source, status, reject_reason FROM jargon_candidates ORDER BY count DESC, word ASC"
                ).fetchall()
                db_candidates = [
                    {"id": r[0], "word": r[1], "reason": r[2], "count": r[3], "source": r[4], "status": r[5], "reject_reason": r[6]}
                    for r in rows
                ]
                by_word = {item.get("word"): dict(item) for item in candidates if isinstance(item, dict)}
                for item in db_candidates:
                    by_word[item["word"]] = item
                candidates = list(by_word.values())
            if _table_exists(c.db.conn, "jargon_blocklist"):
                rows = c.db.conn.execute("SELECT word, reason FROM jargon_blocklist ORDER BY word ASC").fetchall()
                for r in rows:
                    blocked[str(r[0])] = str(r[1] or "manual_block")
        except Exception:
            pass
            
    # 2. 查询数据库中已激活的条目 (增加 c.db 非空安全卫士防御，防止早期请求崩溃)
    db_items = {}
    if c.db and hasattr(c.db, "conn") and c.db.conn and _table_exists(c.db.conn, "jargon"):
        try:
            rows = c.db.conn.execute(
                "SELECT id, word, meaning, status FROM jargon WHERE scope = 'global' AND source = 'holyman_skills' AND is_jargon = 1 AND status = 'confirmed'"
            ).fetchall()
            for r in rows:
                db_items[r[1]] = {"id": r[0], "meaning": r[2], "status": r[3]}
        except Exception:
            pass
            
    # 3. 构造 items 组合结果：兼容旧版 string value 和新版 structured value
    items = []
    local_version = phrases.get("_version", "Unknown")
    for word, raw_value in content_entries(phrases).items():
        item = _normalize_holyman_phrase(word, raw_value)
        example = None
        for raw_example in examples:
            text = raw_example.get("text", "") if isinstance(raw_example, dict) else str(raw_example)
            linked_terms = raw_example.get("linked_terms", []) if isinstance(raw_example, dict) else []
            if word in linked_terms or (text and word in text):
                example = text.strip()
                if len(example) > 150:
                    example = example[:147] + "..."
                break

        item["example"] = example
        if word in db_items:
            item.update({
                "is_activated": True,
                "db_id": db_items[word]["id"],
                "custom_meaning": db_items[word]["meaning"],
            })
        else:
            item.update({
                "is_activated": False,
                "db_id": None,
                "custom_meaning": None,
            })
        items.append(item)

    categories = _build_holyman_categories(items)
    local_count = len(items)

    # 4. 获取远程最新提交的版本哈希；是否提示更新由本地内容健康度决定，避免 commit 字符串误报。
    remote_version = "Unknown"
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.github.com/repos/ykdeso/holyman-skills/commits/main",
            headers={"User-Agent": "WaveMemory-WebUI"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
            sha = data.get("sha", "")[:7]
            date = data.get("commit", {}).get("committer", {}).get("date", "")[:10]
            remote_version = f"{date}-{sha}"
    except Exception:
        pass

    content_status = _holyman_content_status(phrases, local_count, remote_version, quality_report)
    corpus_summary = {
        "count": len(corpus) if isinstance(corpus, list) else 0,
        "safe_for_prompt": False,
        "reference_only": True,
    }

    return jsonify({
        "items": items,
        "phrases": items,
        "concepts": concepts if isinstance(concepts, list) else [],
        "examples": examples if isinstance(examples, list) else [],
        "corpus_summary": corpus_summary,
        "candidates": candidates if isinstance(candidates, list) else [],
        "blocked": blocked if isinstance(blocked, dict) else {},
        "quality_report": quality_report if isinstance(quality_report, dict) else {},
        "categories": categories,
        "local_count": local_count,
        "local_version": local_version,
        "remote_version": remote_version,
        "remote_commit_version": content_status["remote_commit_version"],
        "content_count": content_status["content_count"],
        "content_hash": content_status["content_hash"],
        "asset_status": content_status["asset_status"],
        "is_update_available": content_status["is_update_available"],
    })


@jargon_bp.route("/holyman/toggle", methods=["POST"])
@require_auth
async def toggle_holyman():
    """激活或去激活预设 Holyman 词条。"""
    c = get_container()
    if not _table_exists(c.db.conn, "jargon"):
        return jsonify({"ok": False, "error": "jargon table not found"}), 500
        
    body = await request.get_json() or {}
    word = body.get("word", "").strip()
    meaning = body.get("meaning", "").strip()
    activate = body.get("activate", False)
    
    if not word:
        return jsonify({"ok": False, "error": "word is required"}), 400
        
    now = int(time.time())
    
    if activate:
        # 双重检查是否已存在
        dup = c.db.conn.execute(
            "SELECT id FROM jargon WHERE word = ? AND scope = 'global' AND source = 'holyman_skills'", 
            (word,)
        ).fetchone()
        
        if dup:
            return jsonify({"ok": True, "db_id": dup[0]})
            
        c.db.conn.execute(
            "INSERT INTO jargon (word, meaning, is_jargon, status, frequency, confidence, is_global, group_id, contexts, created_at, updated_at, scope, source) VALUES (?, ?, 1, 'confirmed', 5, 0.9, 1, 'global_fallback', '[]', ?, ?, 'global', 'holyman_skills')",
            (word, meaning, now, now)
        )
        c.db.conn.commit()
        new_id = c.db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return jsonify({"ok": True, "db_id": new_id})
    else:
        c.db.conn.execute(
            "DELETE FROM jargon WHERE word = ? AND scope = 'global' AND source = 'holyman_skills'",
            (word,)
        )
        c.db.conn.commit()
        return jsonify({"ok": True})


@jargon_bp.route("/holyman/sync", methods=["POST"])
@require_auth
async def sync_holyman():
    """同步 Holyman 词库。"""
    body = await request.get_json() or {}
    use_proxy = body.get("use_proxy", True)
    
    from ...services.jargon.sync import HolymanSyncService
    sync_service = HolymanSyncService()
    
    res = await sync_service.sync_from_github(use_proxy=use_proxy)
    if res.get("ok"):
        c = get_container()
        if hasattr(c, "jargon_service") and c.jargon_service:
            if hasattr(c.jargon_service, "_holyman") and c.jargon_service._holyman:
                c.jargon_service._holyman.reload()
                
    return jsonify(res)