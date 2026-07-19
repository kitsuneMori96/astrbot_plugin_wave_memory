import sqlite3
import tempfile
import unittest
from pathlib import Path


class AgentFeedbackWebUITest(unittest.TestCase):
    def _conn(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "agent_feedback.db")
        self.addCleanup(conn.close)
        return conn

    def test_agent_feedback_payload_lists_feedback_suggestions_candidates_and_history(self):
        from services.injection.feedback_store import MemoryFeedbackStore
        from services.injection.config_suggestion_store import ConfigSuggestionStore
        from services.review.candidate_store import ReviewCandidateStore
        from webui.blueprints.agent_feedback import build_agent_feedback_payload

        conn = self._conn()
        feedback_store = MemoryFeedbackStore(conn)
        suggestion_store = ConfigSuggestionStore(conn)
        candidate_store = ReviewCandidateStore(conn)
        feedback_store.record(trace_id="trace-a", memory_id=7, feedback="useful", reason="命中准确")
        suggestion_store.create(scope="channel", channel="memory", problem="slow", evidence_trace_ids=["trace-a"], suggestion="降低 top_k")
        candidate_store.create(candidate_type="belief", content="候选信念", evidence=["trace-a"], reason="需要审查")

        payload = build_agent_feedback_payload(conn)

        self.assertEqual(payload["summary"]["feedback_records"], 1)
        self.assertEqual(payload["summary"]["pending_suggestions"], 1)
        self.assertEqual(payload["summary"]["pending_candidates"], 1)
        self.assertEqual(payload["feedback_records"][0]["feedback"], "useful")
        self.assertEqual(payload["config_suggestions"][0]["review_status"], "pending")
        self.assertEqual(payload["review_candidates"][0]["candidate_type"], "belief")

    def test_agent_feedback_review_actions_do_not_auto_apply_dangerous_suggestions(self):
        from services.injection.config_suggestion_store import ConfigSuggestionStore
        from services.review.candidate_store import ReviewCandidateStore
        from webui.blueprints.agent_feedback import review_config_suggestion, review_review_candidate

        conn = self._conn()
        suggestion_store = ConfigSuggestionStore(conn)
        candidate_store = ReviewCandidateStore(conn)
        suggestion_id = suggestion_store.create(scope="global", problem="too_much", evidence_trace_ids=["trace-danger"], suggestion="关闭 safety 以减少过滤")
        candidate_id = candidate_store.create(candidate_type="jargon", content="候选黑话", evidence=["trace-danger"], reason="人工看看")

        approved = review_config_suggestion(suggestion_store, suggestion_id, "approve")
        rejected = review_review_candidate(candidate_store, candidate_id, "reject")

        self.assertEqual(approved["review_status"], "approved")
        self.assertFalse(approved["applied"])
        self.assertIn("不会自动应用", approved["message"])
        self.assertEqual(rejected["review_status"], "rejected")

    def test_frontend_removes_agent_feedback_route_and_compatibility_client(self):
        api = Path("webui/frontend/src/api/review.ts")
        agent_feedback_page = Path("webui/frontend/src/pages/review/AgentFeedbackPage.tsx")
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")
        sidebar = Path("webui/frontend/src/components/layout/WaveSidebar.tsx").read_text(encoding="utf-8")
        channel_config = Path("webui/frontend/src/pages/channels/ChannelConfigPage.tsx").read_text(encoding="utf-8")
        injection = Path("webui/frontend/src/pages/injection/InjectionPage.tsx").read_text(encoding="utf-8")
        self.assertFalse(api.exists())
        self.assertFalse(agent_feedback_page.exists())
        self.assertNotIn("/agent-feedback", routes)
        self.assertNotIn("/agent-feedback", sidebar)
        self.assertNotIn("getAgentFeedback", channel_config)
        self.assertNotIn("reviewConfigSuggestion", channel_config)
        self.assertNotIn("config_suggestions", channel_config)
        self.assertNotIn("feedback_records", injection)
        self.assertNotIn("group_id", injection)


if __name__ == "__main__":
    unittest.main()
