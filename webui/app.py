"""Quart 应用工厂"""

from __future__ import annotations

from pathlib import Path

from quart import Quart, request

from astrbot.api import logger


def _enable_cors(app: Quart) -> None:
    """手动 CORS（不依赖 quart-cors 包）。"""

    @app.after_request
    async def _add_cors_headers(response):
        origin = request.headers.get("Origin")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers.setdefault(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With",
        )
        response.headers.setdefault(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, PATCH, DELETE, OPTIONS",
        )
        return response


def create_app() -> Quart:
    """创建并配置 Quart 应用。"""
    static_dir = Path(__file__).parent / "static"
    app = Quart(
        __name__,
        static_folder=str(static_dir) if static_dir.exists() else None,
        static_url_path="/static",
    )
    app.secret_key = "wavememory-webui"

    _enable_cors(app)

    # 注册 Blueprint
    from .blueprints import get_blueprints

    for bp in get_blueprints():
        app.register_blueprint(bp)
        logger.debug(f"[WaveMemory WebUI] registered blueprint: {bp.name}")

    return app
