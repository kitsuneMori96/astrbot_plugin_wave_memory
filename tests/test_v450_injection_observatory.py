from pathlib import Path


class TestV450InjectionObservatory:
    def test_injection_page_declares_channel_dropdown_contract(self):
        page = Path("webui/frontend/src/pages/injection/InjectionPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "channelFilterOptions",
            "SelectItem value=\"all\"",
            "全部",
            "safety",
            "memory",
            "timeline",
            "facts",
            "persona",
            "belief",
            "jargon",
            "fewshot",
            "book_lore",
            "fts5",
            "affinity",
        ):
            assert marker in page

        assert "id=\"trace-channel\"" not in page

    def test_injection_page_declares_trace_list_v450_columns(self):
        page = Path("webui/frontend/src/pages/injection/InjectionPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "traceHitChannelCount",
            "traceSkippedChannelCount",
            "traceErrorChannelCount",
            "tracePrimaryTokenChannel",
            "命中通道数",
            "跳过通道数",
            "错误通道数",
            "主 token 消耗通道",
        ):
            assert marker in page

    def test_trace_detail_sheet_declares_structure_and_management_links(self):
        sheet = Path("webui/frontend/src/pages/injection/TraceDetailSheet.tsx").read_text(encoding="utf-8")

        for marker in (
            "managementRouteForChannel",
            "memory item -> /memories?id=...",
            "belief -> /beliefs?id=...",
            "jargon -> /jargon?id=...",
            "fewshot -> /blackbox/fewshot?id=...",
            "book_lore -> /blackbox/book-lore?id=...",
            "facts -> /blackbox/facts?id=...",
            "请求上下文",
            "通道瀑布",
            "命中项",
            "过滤项",
            "最终注入文本",
            "错误/警告",
            "反馈与修正入口",
            "观测和验证，不承载对象本体管理",
        ):
            assert marker in sheet
