"""认证中间件 — token 校验 + require_auth 装饰器"""

from __future__ import annotations

import secrets
from functools import wraps
from typing import Callable

from quart import request, jsonify

from ..container import get_container


# ─── Token 管理 ───


def create_token() -> str:
    """生成新 token 并注册到容器。"""
    container = get_container()
    token = secrets.token_hex(16)
    container.sessions.add(token)
    return token


def verify_token(token: str) -> bool:
    """验证 token 有效性。"""
    container = get_container()
    return token in container.sessions


# ─── 中间件 ───


def require_auth(f: Callable) -> Callable:
    """Blueprint 路由装饰器：无密码直接放行，有密码校验 Bearer token。"""

    @wraps(f)
    async def decorated(*args, **kwargs):
        container = get_container()
        # 无密码模式直接通过
        if not container.password:
            return await f(*args, **kwargs)
        # 校验 token
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.replace("Bearer ", "").strip()
        if not token or not verify_token(token):
            # 学习中心及旧客户端均可读取稳定错误码；保留 detail 兼容旧前端。
            return jsonify({
                "error": {
                    "code": "unauthorized",
                    "message": "Unauthorized",
                    "retryable": False,
                },
                "code": "unauthorized",
                "message": "Unauthorized",
                "retryable": False,
                "detail": "Unauthorized",
            }), 401
        return await f(*args, **kwargs)

    return decorated
