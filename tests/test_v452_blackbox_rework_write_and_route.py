import json
from pathlib import Path


class TestV452BlackboxReworkWriteAndRoute:
    def test_blackbox_py_declares_new_write_endpoints_and_transactions(self):
        py_path = Path("webui/blueprints/blackbox.py")
        assert py_path.exists()
        source = py_path.read_text(encoding="utf-8")

        for marker in (
            "/fewshot/examples/<int:example_id>",
            "methods=[\"DELETE\", \"PUT\"]",
            "DELETE FROM few_shot_examples",
            "UPDATE few_shot_examples SET status=?",
            "/facts/<int:fact_id>",
            "DELETE FROM facts WHERE id=?",
            "UPDATE facts SET confidence=?",
            "/book-lore/<string:table_type>/<int:id_val>",
            "book_entities",
            "book_communities",
            "book_relations",
            "book_notes",
            "/indexes/rebuild",
            "rebuild_indexes_action",
            "memory_index.rebuild",
        ):
            assert marker in source

    def test_frontend_api_declares_new_write_methods(self):
        ts_path = Path("webui/frontend/src/api/blackbox.ts")
        assert ts_path.exists()
        source = ts_path.read_text(encoding="utf-8")

        for marker in (
            "updateBlackboxFewShot",
            "deleteBlackboxFewShot",
            "updateBlackboxFact",
            "deleteBlackboxFact",
            "deleteBookLoreItem",
            "rebuildIndexes",
            "method: 'DELETE'",
            "method: 'PUT'",
            "method: 'POST'",
        ):
            assert marker in source

    def test_memories_page_removes_random_2d_nebula_canvas_completely(self):
        page_path = Path("webui/frontend/src/pages/memories/MemoriesPage.tsx")
        assert page_path.exists()
        source = page_path.read_text(encoding="utf-8")

        # 检验假星云、canvas、clusters 以及 2D 散度点状态是否已彻底删除
        for marker in (
            "loadNebulaClusters",
            "setNebulaPoints",
            "setNebulaClusters",
            "canvasRef",
            "animationFrameId",
            "OrbitIcon",
            "星云",
            "clusterColors",
        ):
            assert marker not in source

    def test_independent_html_pages_fix_routing_hash_to_prevent_session_loss(self):
        explore = Path("webui/static/explore.html").read_text(encoding="utf-8")
        maintain = Path("webui/static/maintain.html").read_text(encoding="utf-8")

        # 检验返回路由是否全部指向 React 的 Hash 锚点，从而阻止物理重载将 React session 冲掉
        assert "href=\"/#/blackbox\"" in explore, "explore.html is missing real back route to React Hash"
        assert "href=\"/#/blackbox\"" in maintain, "maintain.html is missing real back route to React Hash"
