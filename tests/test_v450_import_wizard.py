from pathlib import Path


class TestV450ImportWizard:
    def test_import_page_declares_wizard_and_provider_config_boundary(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "importWizardSteps",
            "配置检查",
            "数据源发现",
            "导入预览",
            "执行导入",
            "Tag 提取",
            "结果复核",
            "静态配置",
            "需要重启",
            "影响导入和 Tag 提取",
            "provider 配置块",
        ):
            assert marker in page

    def test_import_page_declares_dry_run_preview_and_task_model(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "dry-run 预览",
            "数据源",
            "总条数",
            "已导入估计",
            "重复估计",
            "将写入 source 类型",
            "是否会 re-embed",
            "统一任务模型",
            "task_id",
            "task_type",
            "import | tag_extract",
            "status",
            "running | done | error | stopped",
            "progress",
            "processed",
            "total",
            "errors",
            "message",
            "中止按钮只对可中止任务显示",
        ):
            assert marker in page

    def test_import_page_declares_review_links(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "结果复核入口",
            "去记忆管理器看新导入数据",
            "去维护工作台看 Tag 审计",
            "去总览看覆盖率变化",
            "to=\"/memories\"",
            "href=\"/maintain\"",
            "to=\"/dashboard\"",
        ):
            assert marker in page
