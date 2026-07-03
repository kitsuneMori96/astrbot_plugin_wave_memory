import unittest


class AgentPermissionPolicyTest(unittest.TestCase):
    def test_allowed_review_and_forbidden_actions_are_classified(self):
        from services.agent.permission_policy import check_agent_action

        allowed = check_agent_action("explain_injection")
        review = check_agent_action("promote_belief")
        forbidden = check_agent_action("batch_delete")

        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.level, "allowed")
        self.assertFalse(review.allowed)
        self.assertTrue(review.requires_review)
        self.assertEqual(review.level, "review_required")
        self.assertFalse(forbidden.allowed)
        self.assertFalse(forbidden.requires_review)
        self.assertEqual(forbidden.level, "forbidden")

    def test_forbidden_actions_have_explicit_reasons(self):
        from services.agent.permission_policy import check_agent_action

        for action in [
            "batch_delete",
            "disable_safety",
            "disable_audit",
            "edit_provider_credentials",
            "edit_other_plugin_config",
            "alter_astrbot_persona",
            "spoof_plugin_identity",
        ]:
            decision = check_agent_action(action)
            self.assertEqual(decision.level, "forbidden")
            self.assertIn("禁止", decision.reason)

    def test_new_agent_tools_declare_allowed_permission_actions(self):
        from services.agent.permission_policy import check_agent_action
        from tools.config_suggestion import WaveMemorySuggestConfigTool
        from tools.injection_explain import WaveMemoryExplainInjectionTool
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        for tool in [
            WaveMemoryExplainInjectionTool(),
            WaveMemoryFeedbackMemoryTool(),
            WaveMemorySuggestConfigTool(),
            WaveMemorySubmitReviewCandidateTool(),
        ]:
            decision = check_agent_action(tool.permission_action)
            self.assertTrue(decision.allowed, tool.name)
            self.assertEqual(decision.level, "allowed")


if __name__ == "__main__":
    unittest.main()
