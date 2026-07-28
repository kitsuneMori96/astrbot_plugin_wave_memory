from __future__ import annotations

try:
    from quart import Blueprint, jsonify, request
except Exception:
    class Blueprint:
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func): return func
            return deco
    request = None
    def jsonify(value=None, **kwargs):
        return value if value is not None else kwargs

try:
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:
    def get_container(): return None
    def require_auth(func): return func


bindings_bp = Blueprint("bindings", __name__, url_prefix="/api/bindings")


def _db():
    c = get_container()
    db = getattr(c, "db", None)
    return db


@bindings_bp.route("", methods=["GET"])
@require_auth
async def list_bindings():
    db = _db()
    if not db:
        return jsonify({"error": "db unavailable"}), 503
    search = request.args.get("search", "")
    bot_id = request.args.get("bot_id") or None
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))
    items = db.get_bindings(bot_id=bot_id, search=search, limit=limit, offset=offset)
    total = db.count_bindings(bot_id=bot_id, search=search)
    return jsonify({"items": items, "total": total, "limit": limit, "offset": offset})


@bindings_bp.route("", methods=["POST"])
@require_auth
async def create_binding():
    db = _db()
    if not db:
        return jsonify({"error": "db unavailable"}), 503
    data = await request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    local_id = data.get("local_id", "").strip()
    master_id = data.get("master_id", "").strip()
    platform = data.get("platform", "qq").strip().lower()
    bot_id = data.get("bot_id", "yushu").strip()
    if not local_id or not master_id:
        return jsonify({"error": "local_id and master_id are required"}), 400
    if local_id == master_id:
        return jsonify({"error": "local_id and master_id must be different"}), 400
    result = db.add_binding(local_id, master_id, bot_id, platform)
    return jsonify(result), 201


@bindings_bp.route("/<int:binding_id>", methods=["DELETE"])
@require_auth
async def delete_binding(binding_id: int):
    db = _db()
    if not db:
        return jsonify({"error": "db unavailable"}), 503
    db.remove_binding(binding_id)
    return jsonify({"ok": True})
