"""Beliefs Blueprint — 信念管理 CRUD + approve/archive (US-1.2)"""

from __future__ import annotations

import time

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

beliefs_bp = Blueprint("beliefs", __name__, url_prefix="/api/beliefs")


@beliefs_bp.route("/", methods=["GET"])
@require_auth
async def list_beliefs():
    """列出信念（支持 status 筛选）。"""
    c = get_container()
    status = request.args.get("status")  # pending / active / archived
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    sql = "SELECT id, content, type, strength, bot_id, sources, status, created_at, last_reinforced FROM beliefs WHERE 1=1"
    params = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = c.db.conn.execute(sql, params).fetchall()
    total = c.db.conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0]
    pending_count = c.db.conn.execute("SELECT COUNT(*) FROM beliefs WHERE status = 'pending'").fetchone()[0]

    items = [
        {"id": r[0], "content": r[1], "type": r[2], "confidence": r[3],
         "source": r[4], "sources": r[5], "status": r[6],
         "created_at": r[7], "updated_at": r[8]}
        for r in rows
    ]
    return jsonify({"items": items, "total": total, "pending_count": pending_count})


@beliefs_bp.route("/<int:belief_id>/approve", methods=["POST"])
@require_auth
async def approve_belief(belief_id: int):
    """审核通过：pending → active。"""
    c = get_container()
    c.db.conn.execute(
        "UPDATE beliefs SET status = 'active', last_reinforced = ? WHERE id = ? AND status IN ('pending','challenged')",
        (int(time.time()), belief_id),
    )
    c.db.conn.commit()
    return jsonify({"ok": True, "belief_id": belief_id, "new_status": "active"})


@beliefs_bp.route("/<int:belief_id>/archive", methods=["POST"])
@require_auth
async def archive_belief(belief_id: int):
    """归档信念。"""
    c = get_container()
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
