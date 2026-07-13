from pathlib import Path


class TestV450DashboardActions:
    def test_dashboard_replaces_fake_clutter_with_巅峰待办中心(self):
        page = Path("webui/frontend/src/pages/dashboard/DashboardPage.tsx").read_text(encoding="utf-8")

        # 旧的多余指标和凑数死格子应当被彻底物理剔除
        for fake in (
            "能力状态矩阵",
            "发现 -> 管理 -> 验证 -> 调参",
            "无标签记忆数量",
            "Tag 低覆盖",
            "BookLore 索引缺失",
            "FewShot 待审候选",
            "注入通道错误",
            "配置校验失败",
        ):
            assert fake not in page, f"Fake placeholder '{fake}' is still present in dashboard"

        # 应真实宣告 100% 后端驱动的系统待办与自愈隐退逻辑
        for marker in (
            "系统待办",
            "系统状态正常",
            "当前暂无需要人工干预的系统待办事项",
            "记忆标签待复核",
            "风格特征范例待审核",
            "注入链路错误记录",
            "todos?.untagged_count",
            "todos?.pending_fewshot",
            "todos?.has_errors",
        ):
            assert marker in page, f"Expected dashboard component logic but missed: {marker}"

    def test_dashboard_health_uses_summary_severity_instead_of_raw_non_ok_as_error(self):
        card = Path("webui/frontend/src/pages/dashboard/SystemHealthCard.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/system.ts").read_text(encoding="utf-8")
        page = Path("webui/frontend/src/pages/dashboard/DashboardPage.tsx").read_text(encoding="utf-8")

        assert "services_summary" in api
        assert "summary={data.system?.services_summary}" in page
        assert "services.some((service) => service.status !== 'ok')" not in card
        for marker in (
            "summary?.overall",
            "service.severity",
            "可用但降级",
            "部分能力未启用",
            "severity === 'critical'",
        ):
            assert marker in card

    def test_dashboard_injection_summary_explains_window_sum_and_timeline_config(self):
        page = Path("webui/frontend/src/pages/dashboard/DashboardPage.tsx").read_text(encoding="utf-8")
        trend_card = Path("webui/frontend/src/pages/dashboard/InjectionTrendCard.tsx").read_text(encoding="utf-8")
        dashboard_surface = page + "\n" + trend_card
        api = Path("webui/frontend/src/api/system.ts").read_text(encoding="utf-8")

        assert "window?:" in api
        for marker in (
            "getChannelConfig",
            "TimelineConfigSummary",
            "timelineConfig",
            "token_budget",
            "max_items",
            "to=\"/channels\"",
            "窗口累计",
            "单次均值",
            "日均 token",
            "avg_tokens_per_day",
            "sample_count",
        ):
            assert marker in dashboard_surface

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
