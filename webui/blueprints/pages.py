"""HTML 页面路由 Blueprint"""

from pathlib import Path

try:
    from quart import Blueprint
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时的轻量兜底
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco


pages_bp = Blueprint("pages", __name__)

_STATIC_DIR = Path(__file__).parent.parent / "static"
_HTML_HEADERS = {"Content-Type": "text/html"}
_APP_MISSING = "<h1>Wave Memory WebUI</h1><p>React WebUI build not found</p>"


def _html_response(path: Path, fallback: str) -> tuple[str, int, dict[str, str]]:
    if path.exists():
        return path.read_text(encoding="utf-8"), 200, _HTML_HEADERS
    return fallback, 200, _HTML_HEADERS


@pages_bp.route("/")
async def index():
    return _html_response(_STATIC_DIR / "app" / "index.html", _APP_MISSING)


@pages_bp.route("/explore")
async def explore():
    return _html_response(_STATIC_DIR / "explore.html", "<h1>Wave Memory</h1><p>explore.html not found</p>")


@pages_bp.route("/maintain")
async def maintain():
    return _html_response(_STATIC_DIR / "maintain.html", "<h1>Wave Memory</h1><p>maintain.html not found</p>")
