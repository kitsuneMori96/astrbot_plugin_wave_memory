import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _load_pages_module():
    class Blueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

        def app_errorhandler(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    previous_quart = sys.modules.get("quart")
    sys.modules["quart"] = types.SimpleNamespace(Blueprint=Blueprint)
    try:
        import webui.blueprints.pages as pages
        return importlib.reload(pages)
    finally:
        if previous_quart is None:
            sys.modules.pop("quart", None)
        else:
            sys.modules["quart"] = previous_quart


class WebUIPagesRoutingTest(unittest.IsolatedAsyncioTestCase):
    def test_react_explore_iframe_targets_registered_standalone_route(self):
        source = Path("webui/frontend/src/pages/PlaceholderPage.tsx").read_text(encoding="utf-8")
        self.assertIn("`/explore?${query.toString()}`", source)
        self.assertNotIn("`/explore.html?${query.toString()}`", source)

    async def test_index_serves_built_react_app_only(self):
        pages = _load_pages_module()
        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "app").mkdir()
            (static_dir / "app" / "index.html").write_text("<html>React WebUI</html>", encoding="utf-8")
            (static_dir / "index.html").write_text("<html>obsolete page</html>", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                body, status, headers = await pages.index()
            finally:
                pages._STATIC_DIR = original_static_dir
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertIn("React WebUI", body)
        self.assertNotIn("obsolete page", body)

    async def test_index_fails_closed_when_built_app_is_missing(self):
        pages = _load_pages_module()
        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            pages._STATIC_DIR = Path(tmp)
            try:
                body, status, _ = await pages.index()
            finally:
                pages._STATIC_DIR = original_static_dir
        self.assertEqual(status, 503)
        self.assertIn("built WebUI is unavailable", body)

    async def test_explore_remains_a_product_entry_and_maintain_redirects(self):
        pages = _load_pages_module()
        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "explore.html").write_text("Explore Scoped Read Only", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                explore_body, explore_status, explore_headers = await pages.explore()
                _, maintain_status, maintain_headers = await pages.maintain()
            finally:
                pages._STATIC_DIR = original_static_dir
        self.assertEqual(explore_status, 200)
        self.assertEqual(explore_headers["Content-Type"], "text/html")
        self.assertIn("Explore Scoped Read Only", explore_body)
        self.assertEqual(maintain_status, 302)
        self.assertEqual(maintain_headers["Location"], "/#/maintenance")


if __name__ == "__main__":
    unittest.main()
