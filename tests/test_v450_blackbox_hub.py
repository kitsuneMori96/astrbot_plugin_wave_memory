from pathlib import Path


class TestV450BlackboxHub:
    def test_blackbox_route_and_sidebar_entry_exist(self):
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")
        sidebar = Path("webui/frontend/src/components/layout/WaveSidebar.tsx").read_text(encoding="utf-8")

        assert "BlackboxHubPage" in routes
        assert "@/pages/blackbox/BlackboxHubPage" in routes
        assert "path: '/blackbox'" in routes
        assert "title: '黑盒管理'" in routes
        assert "黑盒管理" in sidebar

    def test_blackbox_hub_declares_capability_matrix(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxHubPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "黑盒管理矩阵",
            "BookLore 书设知识",
            "FewShot 风格范例",
            "Facts 事实关系",
            "人物与好感度",
            "索引与 FTS5 健康",
            "查看世界观与书设知识库",
            "查看风格范例候选库",
            "管理稳定事实关系网络",
            "查看用户画像登记表",
            "检查向量索引、FTS5、EPA basis 健康状态",
            "v4.5.4 Rework",
        ):
            assert marker in page

    def test_blackbox_child_routes_are_registered_and_linked(self):
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")
        hub = Path("webui/frontend/src/pages/blackbox/BlackboxHubPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "BlackboxBookLorePage",
            "BlackboxFewShotPage",
            "BlackboxFactsPage",
            "BlackboxPeoplePage",
            "BlackboxIndexesPage",
            "path: '/blackbox/book-lore'",
            "path: '/blackbox/fewshot'",
            "path: '/blackbox/facts'",
            "path: '/blackbox/people'",
            "path: '/blackbox/indexes'",
        ):
            assert marker in routes

        for marker in (
            "to={capability.route}",
            "进入管理",
        ):
            assert marker in hub

    def test_blackbox_child_pages_declare_readonly_management_contracts(self):
        expected_pages = {
            "BlackboxBookLorePage.tsx": (
                "BookLore 世界观书设知识库",
                "entitiesPayload?.items",
                "communitiesPayload?.items",
                "relationsPayload?.items",
                "notesPayload?.items",
                "handleDeleteItem",
            ),
            "BlackboxFewShotPage.tsx": (
                "FewShot 风格范例候选库",
                "examplesPayload?.items",
                "summary?.counts?.pending",
                "summary?.average_score",
                "handleUpdateStatus",
                "handleDeleteFewShot",
            ),
            "BlackboxFactsPage.tsx": (
                "Facts 事实关系网络",
                "factsPayload?.items",
                "factsPayload?.total",
                "subject",
                "predicate",
                "object",
                "handleSaveConfidence",
                "handleDeleteFact",
            ),
            "BlackboxPeoplePage.tsx": (
                "人物与好感度画像",
                "peoplePayload?.items",
                "peoplePayload?.total",
                "qq_id",
                "display_name",
                "bot_id",
                "selectedPerson",
            ),
            "BlackboxIndexesPage.tsx": (
                "索引与 HNSW 对齐健康矩阵",
                "memory vector index",
                "FTS5 index",
                "EPA basis",
                "BookLore HNSW index",
                "handleRebuild",
            ),
        }

        base = Path("webui/frontend/src/pages/blackbox")
        for filename, markers in expected_pages.items():
            path = base / filename
            assert path.exists(), f"missing {filename}"
            page = path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker in page
