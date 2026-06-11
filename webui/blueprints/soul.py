"""Soul Blueprint — 关切/情绪/时间锚点 (US-1.3)"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

soul_bp = Blueprint("soul", __name__, url_prefix="/api")


@soul_bp.route("/concerns", methods=["GET"])
@require_auth
async def list_concerns():
    """查看当前关切列表。"""
    c = get_container()
    group_id = request.args.get("group_id")
    sql = "SELECT id, group_id, content, priority, created_at FROM concerns WHERE 1=1"
    params = []
    if group_id:
        sql += " AND group_id = ?"
        params.append(group_id)
    sql += " ORDER BY priority DESC, created_at DESC LIMIT 50"
    rows = c.db.conn.execute(sql, params).fetchall()
    items = [{"id": r[0], "group_id": r[1], "content": r[2], "priority": r[3], "created_at": r[4]} for r in rows]
    return jsonify({"items": items})


@soul_bp.route("/mood/trajectory", methods=["GET"])
@require_auth
async def mood_trajectory():
    """情绪轨迹（折线图数据）。"""
    c = get_container()
    group_id = request.args.get("group_id")
    limit = int(request.args.get("limit", 100))

    sql = "SELECT group_id, mood_type, intensity, description, created_at FROM bot_mood WHERE 1=1"
    params = []
    if group_id:
        sql += " AND group_id = ?"
        params.append(group_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = c.db.conn.execute(sql, params).fetchall()
    items = [{"group_id": r[0], "type": r[1], "intensity": r[2], "desc": r[3], "ts": r[4]} for r in rows]
    # 时间正序用于前端折线图
    items.reverse()
    return jsonify({"items": items})


@soul_bp.route("/time-anchors", methods=["GET"])
@require_auth
async def time_anchors():
    """时间锚点列表。"""
    c = get_container()
    limit = int(request.args.get("limit", 50))
    rows = c.db.conn.execute(
        "SELECT id, anchor_type, description, timestamp, group_id FROM time_anchors ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    items = [{"id": r[0], "type": r[1], "description": r[2], "timestamp": r[3], "group_id": r[4]} for r in rows]
    return jsonify({"items": items})
