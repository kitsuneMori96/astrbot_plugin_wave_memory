import sqlite3
from pathlib import Path


class TestV454BlackboxRealDataAndAesthetics:
    def test_blackbox_py_uses_real_dual_db_auto_discovery(self):
        py_path = Path("webui/blueprints/blackbox.py")
        assert py_path.exists()
        source = py_path.read_text(encoding="utf-8")

        # 检验是否真正编写了自愈型专属 book_lore.db 发现连接器，不再把主库当成书设源
        for marker in (
            "def _conn_lore_from_container()",
            "candidates = [",
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/book_lore.db",
            "book_lore.db",
            "is_lore_db = True",
            "_conn_lore_from_container()",
            "is_lore_db",
            "conn.close()",
        ):
            assert marker in source

    def test_blackbox_hub_removes_learning_objects_duplicate_card_completely(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxHubPage.tsx").read_text(encoding="utf-8")

        # 学习对象本身就是侧边栏顶级页面，不应该在黑盒控制矩阵重合展示
        assert "学习对象中心" not in page
        assert "/learning-objects" not in page
        assert "DatabaseIcon" not in page
        assert "v4.5.4 Rework" in page

    def test_book_lore_page_uses_tabs_layout_and_has_no_governance_clutter(self):
        page = Path("webui/frontend/src/pages/blackbox/BlackboxBookLorePage.tsx").read_text(encoding="utf-8")

        # 物理剔除所有的假 Sections、governance 纯文本配置和死说明区，把界面还给 Tabs 大表
        assert "governance =" not in page, "booklore page is still cluttered with governance instructions"
        assert "sections=" not in page, "booklore page is still cluttered with fake capability sections"
        assert "BlackboxCapabilityPage" not in page, "booklore page still references fake motherboard template"
        
        # Tabs 真实大表断言
        for marker in (
            "Tabs",
            "TabsList",
            "TabsTrigger",
            "TabsContent",
            "book_entities 实体列表",
            "book_communities 社区世界观",
            "book_relations 关系网络",
            "book_notes 原始笔记",
            "handleDeleteItem",
        ):
            assert marker in page
