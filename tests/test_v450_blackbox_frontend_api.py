from pathlib import Path


class TestV450BlackboxFrontendApi:
    def test_blackbox_frontend_api_client_declares_book_lore_readonly_calls(self):
        api_path = Path("webui/frontend/src/api/blackbox.ts")
        assert api_path.exists(), "blackbox frontend API client is missing"
        source = api_path.read_text(encoding="utf-8")

        for marker in (
            "BlackboxBookLoreSummary",
            "BlackboxListPayload",
            "BlackboxBookLoreEntity",
            "BlackboxBookLoreCommunity",
            "BlackboxBookLoreRelation",
            "BlackboxBookLoreNote",
            "getBlackboxBookLoreSummary",
            "getBlackboxBookLoreEntities",
            "getBlackboxBookLoreCommunities",
            "getBlackboxBookLoreRelations",
            "getBlackboxBookLoreNotes",
            "fetchJson<BlackboxBookLoreSummary>('/api/blackbox/book-lore/summary')",
            "fetchJson<BlackboxListPayload<BlackboxBookLoreEntity>>(`/api/blackbox/book-lore/entities",
            "fetchJson<BlackboxListPayload<BlackboxBookLoreCommunity>>(`/api/blackbox/book-lore/communities",
            "fetchJson<BlackboxListPayload<BlackboxBookLoreRelation>>(`/api/blackbox/book-lore/relations",
            "fetchJson<BlackboxListPayload<BlackboxBookLoreNote>>(`/api/blackbox/book-lore/notes",
            "URLSearchParams",
            "limit",
            "offset",
            "search",
            "sort",
        ):
            assert marker in source

    def test_book_lore_page_uses_real_blackbox_api_with_loading_error_empty_states(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxBookLorePage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useEffect",
            "useState",
            "getBlackboxBookLoreSummary",
            "getBlackboxBookLoreEntities",
            "getBlackboxBookLoreCommunities",
            "getBlackboxBookLoreRelations",
            "getBlackboxBookLoreNotes",
            "BookLore 数据列表",
            "BookLore communities",
            "BookLore relations",
            "BookLore notes",
            "source_book",
            "summary?.counts?.entities",
            "entitiesPayload?.items",
            "communitiesPayload?.items",
            "relationsPayload?.items",
            "notesPayload?.items",
            "BookLore 只读数据加载中",
            "BookLore 数据读取失败",
            "暂无 BookLore entities",
        ):
            assert marker in page

        assert "value: '待接入'" not in page
        assert "后续 communities / relations / notes 分页继续接入" not in page

    def test_blackbox_frontend_api_client_declares_fewshot_readonly_calls(self):
        source = Path("webui/frontend/src/api/blackbox.ts").read_text(encoding="utf-8")

        for marker in (
            "BlackboxFewShotSummary",
            "BlackboxFewShotExample",
            "getBlackboxFewShotSummary",
            "getBlackboxFewShotExamples",
            "fetchJson<BlackboxFewShotSummary>('/api/blackbox/fewshot/summary')",
            "fetchJson<BlackboxListPayload<BlackboxFewShotExample>>(`/api/blackbox/fewshot/examples",
            "status",
            "bot_id",
            "traits",
            "score",
        ):
            assert marker in source

    def test_fewshot_page_uses_real_blackbox_api_with_loading_error_empty_states(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxFewShotPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useEffect",
            "useState",
            "getBlackboxFewShotSummary",
            "getBlackboxFewShotExamples",
            "FewShot 范例列表",
            "examplesPayload?.items",
            "summary?.counts?.pending",
            "summary?.average_score",
            "FewShot 只读数据加载中",
            "FewShot 数据读取失败",
            "暂无 FewShot examples",
        ):
            assert marker in page

        assert "value: '待接入'" not in page

    def test_blackbox_frontend_api_client_declares_remaining_readonly_calls(self):
        source = Path("webui/frontend/src/api/blackbox.ts").read_text(encoding="utf-8")

        for marker in (
            "BlackboxFactItem",
            "BlackboxPersonItem",
            "BlackboxIndexesSummary",
            "getBlackboxFacts",
            "getBlackboxPeople",
            "getBlackboxIndexesSummary",
            "getBlackboxIndexesCheck",
            "fetchJson<BlackboxListPayload<BlackboxFactItem>>(`/api/blackbox/facts",
            "fetchJson<BlackboxListPayload<BlackboxPersonItem>>(`/api/blackbox/people",
            "fetchJson<BlackboxIndexesSummary>('/api/blackbox/indexes/summary')",
            "fetchJson<BlackboxIndexesSummary>('/api/blackbox/indexes/check')",
            "subject",
            "predicate",
            "object",
            "qq_id",
            "display_name",
            "memory_vector_index",
            "fts5_index",
        ):
            assert marker in source

    def test_facts_page_uses_real_blackbox_api_with_loading_error_empty_states(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxFactsPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useEffect",
            "useState",
            "getBlackboxFacts",
            "Facts 关系列表",
            "factsPayload?.items",
            "factsPayload?.total",
            "subject",
            "predicate",
            "object",
            "Facts 只读数据加载中",
            "Facts 数据读取失败",
            "暂无 facts 关系",
        ):
            assert marker in page

        assert "value: '待接入'" not in page

    def test_people_page_uses_real_blackbox_api_with_loading_error_empty_states(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxPeoplePage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useEffect",
            "useState",
            "getBlackboxPeople",
            "人物画像列表",
            "peoplePayload?.items",
            "peoplePayload?.total",
            "BotProfile.db_id，不是 QQ 号",
            "qq_id",
            "display_name",
            "bot_id",
            "People 只读数据加载中",
            "People 数据读取失败",
            "暂无 people/profile 数据",
        ):
            assert marker in page

        assert "value: '待接入'" not in page

    def test_indexes_page_uses_real_blackbox_api_with_loading_error_empty_states(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxIndexesPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "useEffect",
            "useState",
            "getBlackboxIndexesSummary",
            "getBlackboxIndexesCheck",
            "索引健康矩阵",
            "summary?.counts?.memories",
            "summary?.counts?.memories_missing_vector",
            "summary?.health?.fts5_index",
            "Indexes 只读数据加载中",
            "Indexes 数据读取失败",
            "只读诊断完成",
        ):
            assert marker in page

        assert "value: '待接入'" not in page
