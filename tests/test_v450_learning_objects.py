from pathlib import Path


class TestV450LearningObjects:
    def test_learning_objects_page_declares_structured_review_contract(self):
        page = Path("webui/frontend/src/pages/review/LearningObjectsPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "learningObjectFilterOptions",
            "pending / risky / duplicate",
            "结构化候选卡片",
            "pending",
            "risky",
            "duplicate",
            "写入路径",
            "存储",
            "注入通道",
            "风险",
            "运行模式禁用原因",
            "运行模式禁用原因：",
        ):
            assert marker in page

        assert "JSON.stringify(item, null, 2)" not in page

    def test_learning_objects_page_declares_candidate_review_actions(self):
        page = Path("webui/frontend/src/pages/review/LearningObjectsPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "reviewCandidate",
            "handleReviewCandidate",
            "approve",
            "reject",
            "批准",
            "拒绝",
            "候选可 approve/reject",
        ):
            assert marker in page

    def test_learning_objects_page_declares_blackbox_cross_links(self):
        page = Path("webui/frontend/src/pages/review/LearningObjectsPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "learningObjectLinks",
            "BookLore",
            "FewShot",
            "Facts",
            "People",
            "to=\"/blackbox/book-lore\"",
            "to=\"/blackbox/fewshot\"",
            "to=\"/blackbox/facts\"",
            "to=\"/blackbox/people\"",
        ):
            assert marker in page
