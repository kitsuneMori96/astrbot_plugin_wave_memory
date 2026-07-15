from pathlib import Path


class TestV450MemoriesSourceAware:
    def test_memories_page_declares_source_asset_semantics(self):
        page = Path("webui/frontend/src/pages/memories/MemoriesPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "sourceAssetMetadata",
            "sourceAssetMeta",
            "资产类型语义",
            "live",
            "群聊长期记忆",
            "保持在记忆管理器",
            "bzz_experience",
            "第一人称经历",
            "/soul",
            "book_lore",
            "书设知识",
            "提示去 BookLore 管理",
            "/blackbox/book-lore",
            "bot_reply",
            "Bot 回复素材",
            "可送入 FewShot 候选",
            "/blackbox/fewshot",
            "fewshot",
            "风格范例",
            "提示去 FewShot 管理",
        ):
            assert marker in page

    def test_memories_page_declares_association_and_risk_contracts(self):
        page = Path("webui/frontend/src/pages/memories/MemoriesPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "详情关联区",
            "Tags",
            "Facts",
            "Beliefs",
            "Person links",
            "Injection traces",
            "Similar memories",
            "危险等级",
            "re-embed：中风险",
            "改 source：中风险",
            "删除：高风险，必须二次确认",
            "转为管理对象",
            "Bot 回复 -> FewShot 候选",
            "世界观内容 -> BookLore 条目候选",
            "稳定关系句子 -> Fact 候选",
            "星云视图为只读可视化",
            "不和神经云图职责冲突",
        ):
            assert marker in page
