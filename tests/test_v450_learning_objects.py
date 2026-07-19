from pathlib import Path


class TestStage5LearningCenter:
    def test_learning_process_is_consolidated_into_current_route(self):
        page = Path("webui/frontend/src/pages/learning/LearningCenterPage.tsx").read_text(encoding="utf-8")
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")
        assert "LearningCenterPage" in routes
        assert "path: '/learning'" in routes
        for marker in ("候选", "review_status", "reviewLearningCandidate", "retryLearningPromotion", "target_link"):
            assert marker in page

    def test_learning_center_keeps_process_state_separate_from_domain_truth(self):
        page = Path("webui/frontend/src/pages/learning/LearningCenterPage.tsx").read_text(encoding="utf-8")
        for marker in ("来源", "任务", "晋升", "promotion_status", "不冒充正式样例", "正式 Scope 投影", "quarantined", "target_link"):
            assert marker in page
