from pathlib import Path


class TestV450MaintainWorkbench:
    def test_maintain_page_declares_v450_workbench_sections(self):
        html = Path("webui/static/maintain.html").read_text(encoding="utf-8")

        for marker in (
            "v4.5 完整维护工作台",
            "说明区",
            "任务区",
            "任务配置区",
            "建议处理区",
            "事件区",
            "危险区",
            "用于清理 Tag、关系、索引与任务状态",
            "不直接生成新人格",
            "不直接改变 AstrBot Persona",
            "审计任务默认只生成建议",
            "批准建议可能修改数据库",
            "删除/合并/重建属于危险操作",
        ):
            assert marker in html

    def test_maintain_page_declares_task_catalog_and_config_fields(self):
        html = Path("webui/static/maintain.html").read_text(encoding="utf-8")

        for marker in (
            "Tag 质量审计",
            "Tag 合并建议",
            "Tag 重分类建议",
            "Tag 删除建议",
            "孤立 Tag 检查",
            "低质量关系检查",
            "重复记忆检查",
            "向量索引健康检查",
            "FTS5 索引健康检查",
            "BookLore 索引检查",
            "FewShot 健康检查",
            "EPA basis 重建",
            "只读诊断",
            "生成建议",
            "任务执行",
            "strategy",
            "scan_limit",
            "confidence_threshold",
            "dry_run",
            "generate_suggestions_only",
            "batch_size",
            "timeout_seconds",
            "event_retention_days",
            "影响范围",
        ):
            assert marker in html

    def test_maintain_page_declares_suggestion_event_and_danger_contracts(self):
        html = Path("webui/static/maintain.html").read_text(encoding="utf-8")

        for marker in (
            "目标对象",
            "证据",
            "生成原因",
            "预期影响",
            "是否可回滚",
            "批准后执行动作摘要",
            "任务 ID",
            "任务类型",
            "开始/结束时间",
            "耗时",
            "处理数量",
            "生成建议数量",
            "错误原因",
            "二次确认",
            "批量删除 Tag",
            "批量合并 Tag",
            "重建索引",
            "清空维护建议",
            "清理历史 trace",
        ):
            assert marker in html
