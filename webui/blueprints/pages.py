"""HTML 页面路由 Blueprint"""

from pathlib import Path

from quart import Blueprint

pages_bp = Blueprint("pages", __name__)

_STATIC_DIR = Path(__file__).parent.parent / "static"


@pages_bp.route("/")
async def index():
    path = _STATIC_DIR / "index.html"
    if path.exists():
        return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
    return "<h1>Wave Memory WebUI</h1><p>static/index.html not found</p>", 200, {"Content-Type": "text/html"}


@pages_bp.route("/explore")
async def explore():
    path = _STATIC_DIR / "explore.html"
    if path.exists():
        return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
    return "<h1>Wave Memory</h1><p>explore.html not found</p>", 200, {"Content-Type": "text/html"}


@pages_bp.route("/maintain")
async def maintain():
    path = _STATIC_DIR / "maintain.html"
    if path.exists():
        return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
    return "<h1>Wave Memory</h1><p>maintain.html not found</p>", 200, {"Content-Type": "text/html"}
