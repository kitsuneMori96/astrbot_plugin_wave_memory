"""Soul Blueprint — 关切/情绪/时间锚点 (US-1.3)"""

from __future__ import annotations

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import require_auth

soul_bp = Blueprint("soul", __name__, url_prefix="/api")


@soul_bp.route("/concerns", methods=["GET"])
@require_auth
async def list_concerns():
    """查看当前关切列表（按强度排序）。"""
    c = get_container()
    bot_id = request.args.get("bot_id")
    sql = "SELECT id, topic, intensity, bot_id, origin_memory_id, created_at, last_triggered FROM concerns WHERE 1=1"
    params = []
    if bot_id:
        sql += " AND bot_id = ?"
        params.append(bot_id)
    sql += " ORDER BY intensity DESC, created_at DESC LIMIT 50"
    rows = c.db.conn.execute(sql, params).fetchall()
    items = [
        {"id": r[0], "topic": r[1], "intensity": r[2], "bot_id": r[3],
         "origin_memory_id": r[4], "created_at": r[5], "last_triggered": r[6]}
        for r in rows
    ]
    return jsonify({"items": items})


@soul_bp.route("/mood/trajectory", methods=["GET"])
@require_auth
async def mood_trajectory():
    """情绪轨迹（折线图数据）。"""
    c = get_container()
    group_id = request.args.get("group_id")
    limit = int(request.args.get("limit", 100))

    sql = "SELECT group_id, mood_type, intensity, description, start_time, end_time, is_active FROM bot_mood WHERE 1=1"
    params = []
    if group_id:
        sql += " AND group_id = ?"
        params.append(group_id)
    sql += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    rows = c.db.conn.execute(sql, params).fetchall()
    items = [
        {"group_id": r[0], "type": r[1], "intensity": r[2], "desc": r[3],
         "ts": r[4], "end_time": r[5], "is_active": bool(r[6])}
        for r in rows
    ]
    # 时间正序用于前端折线图
    items.reverse()
    return jsonify({"items": items})


@soul_bp.route("/time-anchors", methods=["GET"])
@require_auth
async def time_anchors():
    """时间锚点列表（情感权重高的关键事件）。"""
    c = get_container()
    bot_id = request.args.get("bot_id")
    limit = int(request.args.get("limit", 50))
    sql = "SELECT id, event_summary, timestamp, emotional_weight, bot_id FROM time_anchors WHERE 1=1"
    params = []
    if bot_id:
        sql += " AND bot_id = ?"
        params.append(bot_id)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    rows = c.db.conn.execute(sql, params).fetchall()
    items = [
        {"id": r[0], "event_summary": r[1], "timestamp": r[2],
         "emotional_weight": r[3], "bot_id": r[4]}
        for r in rows
    ]
    return jsonify({"items": items})
