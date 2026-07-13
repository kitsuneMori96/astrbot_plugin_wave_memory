"""Quart 应用工厂"""

from __future__ import annotations

from pathlib import Path

from quart import Quart, request

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover - 本地单测未安装 AstrBot SDK 时的轻量兜底
    class _Logger:
        def debug(self, *args, **kwargs): pass
    logger = _Logger()


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

    # Stage 3 diagnostics is independently registered so older blueprint registries
    # can load it without gaining any database/provider fallback behavior.
    from .blueprints.diagnostics import diagnostics_bp

    if diagnostics_bp.name not in app.blueprints:
        app.register_blueprint(diagnostics_bp)
        logger.debug(f"[WaveMemory WebUI] registered blueprint: {diagnostics_bp.name}")

    return app
