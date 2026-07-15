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
        assert "记忆与导入" in sidebar
        assert "注入与通道" in sidebar
        assert "认知与审查" in sidebar
        assert "系统与维护" in sidebar

    def test_blackbox_hub_declares_capability_matrix(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxHubPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "黑盒管理前端矩阵",
            "发现 -> 管理 -> 验证 -> 调参",
            "BookLore",
            "FewShot",
            "Facts",
            "人物与好感",
            "索引与 FTS5",
            "学习对象",
            "世界观/书设知识库，不是群聊记忆，不是人格指令",
            "风格范例库，不是事实记忆，不代表真实发生过",
            "事实关系管理入口",
            "人物画像、UserProfile、Affinity",
            "向量索引、FTS5、EPA basis 健康入口",
            "只读入口",
            "危险操作需二次确认",
            "BookLore | FewShot | Facts | 人物 | 索引 | 学习对象",
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
            "implemented: true",
            "to={capability.route}",
            "进入管理页",
        ):
            assert marker in hub

    def test_blackbox_child_pages_declare_readonly_management_contracts(self):
        expected_pages = {
            "BlackboxBookLorePage.tsx": (
                "BookLore 管理",
                "世界观/书设知识库，不是群聊记忆，不是人格指令",
                "实体数",
                "关系数",
                "社区数",
                "notes 数",
                "索引健康",
                "HNSW 文件",
                "id map",
                "BookLore-only 查询",
                "重建索引需二次确认",
            ),
            "BlackboxFewShotPage.tsx": (
                "FewShot 管理",
                "风格范例库，不是事实记忆，不代表真实发生过",
                "pending / approved / rejected",
                "平均 score",
                "漂移检测",
                "测试匹配",
                "批准/拒绝属于后续写操作",
            ),
            "BlackboxFactsPage.tsx": (
                "Facts / 关系管理",
                "稳定事实关系与人物/实体关系，不是自由文本记忆",
                "subject",
                "predicate",
                "object",
                "PERSON_ALIAS",
                "facts channel 测试",
                "删除、合并重复 facts 需二次确认",
            ),
            "BlackboxPeoplePage.tsx": (
                "人物与好感管理",
                "person_registry",
                "user_profiles",
                "affinity dimensions",
                "BotProfile.db_id，不是 QQ 号",
                "关系事件时间线",
                "合并人物为高风险",
            ),
            "BlackboxIndexesPage.tsx": (
                "索引与 FTS5 管理",
                "memory vector index",
                "FTS5 index",
                "EPA basis",
                "BookLore HNSW index",
                "DB 行数 vs index count",
                "只读诊断默认开启",
                "重建需二次确认",
            ),
        }

        base = Path("webui/frontend/src/pages/blackbox")
        for filename, markers in expected_pages.items():
            path = base / filename
            assert path.exists(), f"missing {filename}"
            page = path.read_text(encoding="utf-8")
            for marker in markers:
                assert marker in page
            for shared_marker in (
                "只读诊断",
                "影响范围",
                "生效时机",
                "是否持久化",
                "是否需要重启",
                "回滚方式",
                "加载中",
                "读取失败",
                "暂无数据",
            ):
                assert shared_marker in page
