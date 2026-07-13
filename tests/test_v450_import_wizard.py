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
            "需要重启",
            "provider 配置块",
        ):
            assert marker in page

    def test_import_page_declares_dry_run_preview_without_task_model_clutter(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "dry-run 预览",
            "数据源",
            "总条数",
            "已导入估计",
            "重复估计",
            "将写入 source 类型",
            "是否会 re-embed",
        ):
            assert marker in page

        # 统一任务模型、中止按钮等已被物理移除
        for clutter in (
            "统一任务模型",
            "task_model_fields",
            "中止按钮只对可中止任务显示",
        ):
            assert clutter not in page

    def test_import_page_reads_static_provider_config_from_full_config_api(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")
        config_api = Path("webui/frontend/src/api/config.ts").read_text(encoding="utf-8")

        assert "getFullConfig" in page
        assert "getChannelConfig" not in page
        assert "'/api/config'" in config_api

    def test_three_pages_use_shared_tag_extraction_config_panel(self):
        component = Path("webui/frontend/src/components/tag/TagExtractionConfigPanel.tsx")
        assert component.exists(), "TagExtractionConfigPanel shared component must exist"
        component_source = component.read_text(encoding="utf-8")

        for marker in (
            "getFullConfig",
            "saveFullConfig",
            "embedding_provider_id",
            "embedding_dimension",
            "tag_llm_provider_id",
            "tag_batch_size",
            "tag_write_policy",
            "需要重启",
        ):
            assert marker in component_source, f"Expected shared config component marker but missing: {marker}"

        for page_path in (
            "webui/frontend/src/pages/memories/MemoriesPage.tsx",
            "webui/frontend/src/pages/import/ImportPage.tsx",
            "webui/frontend/src/pages/maintain/MaintainPage.tsx",
        ):
            page = Path(page_path).read_text(encoding="utf-8")
            assert "TagExtractionConfigPanel" in page, f"{page_path} must render the shared tag config panel"
            assert "@/components/tag/TagExtractionConfigPanel" in page, f"{page_path} must import the shared tag config panel"

    def test_all_three_execution_paths_pass_unified_tag_options(self):
        memories_page = Path("webui/frontend/src/pages/memories/MemoriesPage.tsx").read_text(encoding="utf-8")
        import_page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")
        maintain_page = Path("webui/frontend/src/pages/maintain/MaintainPage.tsx").read_text(encoding="utf-8")
        stream_api = Path("webui/frontend/src/api/memories.ts").read_text(encoding="utf-8")
        tags_api = Path("webui/frontend/src/api/tags.ts").read_text(encoding="utf-8")

        assert "TagExecutionOptions" in tags_api
        assert "TagWritePolicy" in tags_api
        assert "payload?: Record<string, unknown>" in stream_api
        assert "JSON.stringify({ ids, ...payload })" in stream_api

        for marker in ("tagBatchSize", "tagWritePolicy", "tag_batch_size", "tag_write_policy"):
            assert marker in memories_page, f"MemoriesPage must pass unified tag option marker: {marker}"
            assert marker in import_page, f"ImportPage must pass unified tag option marker: {marker}"
            assert marker in maintain_page, f"MaintainPage must pass unified tag option marker: {marker}"

    def test_import_page_uses_shared_tag_extraction_config_for_external_import(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "extractTagsOnImport",
            "tagBatchSize",
            "tagWritePolicy",
            "extract_tags=${extractTagsOnImport ? '1' : '0'}",
            "tag_batch_size=${tagBatchSize}",
            "tag_write_policy=${tagWritePolicy}",
            "和标签与维护中心共用同一个 Tag 提取分析 LLM",
            "同步提取 Tag",
        ):
            assert marker in page, f"Expected shared tag extraction config marker but missing: {marker}"

    def test_import_page_declares_review_links_with_spa_nav(self):
        page = Path("webui/frontend/src/pages/import/ImportPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "结果复核入口",
            "去记忆管理器看新导入数据",
            "去标签与维护中心看 Tag 审计",
            "去总览看覆盖率变化",
            'to="/memories"',
            'to="/maintain"',
            'to="/dashboard"',
        ):
            assert marker in page, f"Expected review link marker but missing: {marker}"
