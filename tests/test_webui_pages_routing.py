import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _load_pages_module():
    if "quart" not in sys.modules:
        class Blueprint:
            def __init__(self, *args, **kwargs):
                pass

            def route(self, *args, **kwargs):
                def deco(func):
                    return func
                return deco

        sys.modules["quart"] = types.SimpleNamespace(Blueprint=Blueprint)
    import webui.blueprints.pages as pages
    return importlib.reload(pages)


class WebUIPagesRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_index_prefers_built_react_app_when_present(self):
        pages = _load_pages_module()

        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "app").mkdir()
            (static_dir / "app" / "index.html").write_text("<html><body>React WebUI</body></html>", encoding="utf-8")
            (static_dir / "index.html").write_text("<html><body>Legacy Alpine WebUI</body></html>", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                body, status, headers = await pages.index()
            finally:
                pages._STATIC_DIR = original_static_dir

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertIn("React WebUI", body)
        self.assertNotIn("Legacy Alpine WebUI", body)

    async def test_index_falls_back_to_legacy_when_react_app_missing(self):
        pages = _load_pages_module()

        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "index.html").write_text("<html><body>Legacy Alpine WebUI</body></html>", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                body, status, headers = await pages.index()
            finally:
                pages._STATIC_DIR = original_static_dir

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertIn("Legacy Alpine WebUI", body)

    async def test_legacy_always_serves_old_alpine_page(self):
        pages = _load_pages_module()

        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "app").mkdir()
            (static_dir / "app" / "index.html").write_text("<html><body>React WebUI</body></html>", encoding="utf-8")
            (static_dir / "index.html").write_text("<html><body>Legacy Alpine WebUI</body></html>", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                body, status, headers = await pages.legacy()
            finally:
                pages._STATIC_DIR = original_static_dir

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html")
        self.assertIn("Legacy Alpine WebUI", body)
        self.assertNotIn("React WebUI", body)

    async def test_explore_and_maintain_routes_keep_existing_static_files(self):
        pages = _load_pages_module()

        original_static_dir = pages._STATIC_DIR
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "explore.html").write_text("<html><body>Explore Page</body></html>", encoding="utf-8")
            (static_dir / "maintain.html").write_text("<html><body>Maintain Page</body></html>", encoding="utf-8")
            pages._STATIC_DIR = static_dir
            try:
                explore_body, explore_status, explore_headers = await pages.explore()
                maintain_body, maintain_status, maintain_headers = await pages.maintain()
            finally:
                pages._STATIC_DIR = original_static_dir

        self.assertEqual(explore_status, 200)
        self.assertEqual(explore_headers["Content-Type"], "text/html")
        self.assertIn("Explore Page", explore_body)
        self.assertEqual(maintain_status, 200)
        self.assertEqual(maintain_headers["Content-Type"], "text/html")
        self.assertIn("Maintain Page", maintain_body)


if __name__ == "__main__":
    unittest.main()
