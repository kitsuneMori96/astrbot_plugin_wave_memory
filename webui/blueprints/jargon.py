"""Jargon Blueprint — 黑话管理 WebUI API (US-4.4)"""

from __future__ import annotations

import json
import time

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

jargon_bp = Blueprint("jargon", __name__, url_prefix="/api/jargon")


@jargon_bp.route("/", methods=["GET"])
@require_auth
async def list_jargon():
    """列出黑话（支持 group_id / status 筛选）。"""
    c = get_container()
    group_id = request.args.get("group_id")
    status = request.args.get("status")  # confirmed / pending / rejected
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    sql = "SELECT id, word, meaning, is_jargon, frequency, confidence, is_global, group_id, contexts, created_at FROM jargon WHERE 1=1"
    params = []
    if group_id:
        sql += " AND group_id = ?"
        params.append(group_id)
    if status == "confirmed":
        sql += " AND is_jargon = 1"
    elif status == "pending":
        sql += " AND is_jargon IS NULL"
    elif status == "rejected":
        sql += " AND is_jargon = 0"
    sql += " ORDER BY frequency DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    total = c.db.conn.execute("SELECT COUNT(*) FROM jargon").fetchone()[0]
    items = [
        {"id": r[0], "word": r[1], "meaning": r[2], "is_jargon": r[3],
         "frequency": r[4], "confidence": r[5], "is_global": bool(r[6]),
         "group_id": r[7], "contexts": json.loads(r[8] or "[]"), "created_at": r[9]}
        for r in rows
    ]
    return jsonify({"items": items, "total": total})


@jargon_bp.route("/<int:jargon_id>/review", methods=["POST"])
@require_auth
async def review_jargon(jargon_id: int):
    """审核：approve / reject。"""
    c = get_container()
    body = await request.get_json(silent=True) or {}
    action = body.get("action")  # approve / reject
    meaning = body.get("meaning")  # 可选：修正含义

    if action not in ("approve", "reject"):
        return jsonify({"error": "action must be approve or reject"}), 400

    now = int(time.time())
    if action == "approve":
        sets = "is_jargon = 1, updated_at = ?"
        params = [now]
        if meaning:
            sets += ", meaning = ?"
            params.append(meaning)
        params.append(jargon_id)
        c.db.conn.execute(f"UPDATE jargon SET {sets} WHERE id = ?", params)
    else:
        c.db.conn.execute("UPDATE jargon SET is_jargon = 0, updated_at = ? WHERE id = ?", (now, jargon_id))

    c.db.conn.commit()
    return jsonify({"ok": True, "jargon_id": jargon_id, "action": action})


@jargon_bp.route("/<int:jargon_id>", methods=["PUT"])
@require_auth
async def edit_jargon(jargon_id: int):
    """编辑黑话词条/释义。"""
    c = get_container()
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
    c.db.conn.execute("DELETE FROM jargon WHERE id = ?", (jargon_id,))
    c.db.conn.commit()
    return jsonify({"ok": True, "deleted": jargon_id})


@jargon_bp.route("/<int:jargon_id>/toggle_global", methods=["POST"])
@require_auth
async def toggle_global(jargon_id: int):
    """切换全局状态。"""
    c = get_container()
    row = c.db.conn.execute("SELECT is_global FROM jargon WHERE id = ?", (jargon_id,)).fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    new_val = 0 if row[0] else 1
    c.db.conn.execute("UPDATE jargon SET is_global = ?, updated_at = ? WHERE id = ?", (new_val, int(time.time()), jargon_id))
    c.db.conn.commit()
    return jsonify({"ok": True, "jargon_id": jargon_id, "is_global": bool(new_val)})
