"""认证 Blueprint — 登录 / token 管理"""

from quart import Blueprint, jsonify, request

from ..container import get_container
from ..middleware.auth import create_token

auth_bp = Blueprint("auth", __name__, url_prefix="/api")


@auth_bp.route("/login", methods=["POST"])
async def login():
    """登录获取 token。"""
    container = get_container()
    if not container.password:
        return jsonify({"token": "no-auth", "message": "No password required"})

    body = await request.get_json(silent=True) or {}
    if body.get("password") == container.password:
        token = create_token()
        return jsonify({"token": token, "message": "Login successful"})

    return jsonify({"detail": "Invalid password"}), 401


@auth_bp.route("/auth/check", methods=["GET"])
async def auth_check():
    """检查是否需要认证。"""
    container = get_container()
    return jsonify({"requires_auth": bool(container.password)})
