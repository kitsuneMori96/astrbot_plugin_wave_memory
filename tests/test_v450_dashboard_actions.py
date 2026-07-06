from pathlib import Path


class TestV450DashboardActions:
    def test_dashboard_declares_capability_matrix_and_action_cards(self):
        page = Path("webui/frontend/src/pages/dashboard/DashboardPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "能力状态矩阵",
            "发现 -> 管理 -> 验证 -> 调参",
            "高级检索",
            "BookLore",
            "FewShot",
            "Facts",
            "人物画像/好感",
            "FTS5",
            "注入 Trace",
            "通道配置",
            "/explore",
            "/blackbox/book-lore",
            "/blackbox/fewshot",
            "/blackbox/facts",
            "/blackbox/people",
            "/blackbox/indexes",
            "/injection",
            "/channels",
            "需要处理",
            "无标签记忆数量",
            "Tag 低覆盖",
            "BookLore 索引缺失",
            "FewShot 待审候选",
            "注入通道错误",
            "配置校验失败",
            "/import",
            "/maintain",
        ):
            assert marker in page

    def test_dashboard_module_ranking_links_to_management_pages(self):
        page = Path("webui/frontend/src/pages/dashboard/DashboardPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "moduleRoute",
            "Link to={route}",
            "book_lore_tokens",
            "fewshot_tokens",
            "jargon_tokens",
            "belief_tokens",
            "facts_tokens",
            "/blackbox/book-lore",
            "/blackbox/fewshot",
            "/jargon",
            "/beliefs",
            "/blackbox/facts",
        ):
            assert marker in page
