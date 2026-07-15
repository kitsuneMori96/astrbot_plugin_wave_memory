from pathlib import Path


class TestV440MaintainContract:
    def test_maintain_page_declares_v440_tag_tool_boundary(self):
        html = Path("webui/static/maintain.html").read_text(encoding="utf-8")

        for marker in (
            "这是 Tag 维护工具，不是运行配置页",
            "不会修改 AstrBot 静态配置",
            "完整维护任务中心进入 v4.5.0",
            "审计策略",
            "mixed",
            "lowconf",
            "orphan",
            "duplicate",
            "批准建议会修改 DB",
            "拒绝只更新建议状态",
        ):
            assert marker in html

        assert "low_quality" not in html
        assert "high_freq" not in html
