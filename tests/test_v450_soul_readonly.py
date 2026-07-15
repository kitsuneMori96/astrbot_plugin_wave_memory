from pathlib import Path


class TestV450SoulReadonlyManagement:
    def test_soul_page_declares_readonly_internal_state_contract(self):
        page = Path("webui/frontend/src/pages/soul/SoulPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "soulInternalStatusCards",
            "只读内部状态管理",
            "只读优先",
            "不允许 WebUI 直接覆盖 AstrBot Persona",
            "修改类操作必须进入后续版本",
            "persona evolution 状态",
            "concern tracker 当前关切",
            "desire engine 当前欲望/动机",
            "mood trajectory 当前情绪轨迹",
            "experience episodes",
            "relationship events",
        ):
            assert marker in page

    def test_soul_page_declares_runtime_boundaries_and_visible_metrics(self):
        page = Path("webui/frontend/src/pages/soul/SoulPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "当前 Bot 视角",
            "当前关切数量",
            "经历锚点数量",
            "情绪快照数量",
            "心里话动机",
            "只读诊断",
        ):
            assert marker in page
