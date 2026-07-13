from pathlib import Path


class TestV451BlackboxInteractions:
    def test_blackbox_child_pages_have_real_interaction_controls(self):
        base = Path("webui/frontend/src/pages/blackbox")
        expected = {
            "BlackboxBookLorePage.tsx": (
                "搜索 BookLore",
                "handleSearchSubmit",
                "handleRefresh",
                "上一页",
                "下一页",
                "handleDeleteItem",
                "deleteBookLoreItem",
            ),
            "BlackboxFewShotPage.tsx": (
                "搜索 FewShot",
                "statusFilter",
                "handleSearchSubmit",
                "handleRefresh",
                "上一页",
                "下一页",
                "handleUpdateStatus",
                "handleDeleteFewShot",
            ),
            "BlackboxFactsPage.tsx": (
                "搜索 Facts",
                "factFilter",
                "handleSearchSubmit",
                "handleRefresh",
                "selectedFact",
                "查看证据记忆",
                "to={`/memories?open=${",
                "handleSaveConfidence",
                "handleDeleteFact",
            ),
            "BlackboxPeoplePage.tsx": (
                "搜索人物",
                "handleSearchSubmit",
                "handleRefresh",
                "selectedPerson",
                "人物画像详情",
            ),
            "BlackboxIndexesPage.tsx": (
                "重新检查",
                "handleRefresh",
                "查看缺向量记忆",
                "to=\"/memories?has_vector=false\"",
                "一键重建向量索引",
                "rebuilding",
                "rebuildIndexes",
            ),
        }

        for filename, markers in expected.items():
            page = (base / filename).read_text(encoding="utf-8")
            for marker in markers:
                assert marker in page, f"{filename} missing {marker!r}"

    def test_blackbox_hub_does_not_overpromise_unsupported_write_actions(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxHubPage.tsx").read_text(encoding="utf-8")

        assert "黑盒管理矩阵" in page
        assert "v4.5.4 Rework" in page
        assert "发现 -> 管理 -> 验证 -> 调参" not in page
        assert "只读入口 · 等待独立管理页" not in page

    def test_memories_page_supports_blackbox_url_deep_links_and_stream_state(self):
        page = Path("webui/frontend/src/pages/memories/MemoriesPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useSearchParams",
            "searchParams.get('open')",
            "searchParams.get('has_vector')",
            "searchParams.get('search')",
            "setStreamRunning(true)",
            "setStreamRunning(false)",
            "disabled={streamRunning || selectedIds.length === 0}",
            "处理中",
        ):
            assert marker in page
