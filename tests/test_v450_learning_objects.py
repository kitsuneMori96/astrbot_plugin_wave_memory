from pathlib import Path


class TestV450LearningObjects:
    def test_learning_object_review_frontend_is_consolidated_into_learning_center(self):
        page = Path("webui/frontend/src/pages/review/LearningObjectsPage.tsx").read_text(encoding="utf-8")
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")

        assert 'to="/learning-center"' in page
        assert "getLearningObjectsReview" not in page
        assert "reviewCandidate" not in page
        assert "/learning-objects" not in routes

    def test_learning_center_keeps_structured_candidate_and_history_sections(self):
        page = Path("webui/frontend/src/pages/learning/LearningCenterPage.tsx").read_text(encoding="utf-8")

        for marker in ("候选", "审核状态", "晋升历史", "reviewLearningCandidate", "retryLearningPromotion"):
            assert marker in page
