import unittest


class EffectiveConfigTest(unittest.TestCase):
    @staticmethod
    def _scope(*, bot_id="bot-alpha", conversation="g1", subject="test:user:u1"):
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope(
            bot_id=bot_id,
            visibility="group",
            session=SessionRef(
                id=f"test:group:{conversation}",
                platform_id="test",
                kind="group",
                conversation_id=conversation,
            ),
            subject_principal_id=subject,
        )

    def test_hierarchy_none_falls_back_and_explicit_false_zero_empty_string_survive(self):
        from services.config.effective_config import resolve_effective_config

        scope = self._scope()
        result = resolve_effective_config(
            {
                "feature": {"enabled": True, "limit": 8, "label": "system"},
                "restartable": "old",
            },
            scope=scope,
            bot_config={"feature": {"enabled": False, "limit": None}},
            session_config={"feature": {"limit": 0, "label": None}},
            user_config={"feature": {"label": ""}},
            relationship_config={"feature": {"enabled": None}},
            field_metadata={
                "feature.*": {"apply_mode": "hot"},
                "restartable": {"apply_mode": "restart", "restart_required": True},
            },
        )

        self.assertIs(result.values["feature"]["enabled"], False)
        self.assertEqual(result.values["feature"]["limit"], 0)
        self.assertEqual(result.values["feature"]["label"], "")
        self.assertEqual(result.provenance["feature.enabled"]["layer"], "bot")
        self.assertEqual(result.provenance["feature.limit"]["layer"], "session")
        self.assertEqual(result.provenance["feature.label"]["layer"], "user")
        self.assertTrue(result.restart_required)
        self.assertEqual(result.apply_mode, "restart")
        self.assertIn("restartable", result.restart_paths)

        repeated = resolve_effective_config(
            {"feature": {"enabled": True, "limit": 8, "label": "system"}, "restartable": "old"},
            scope=scope,
            bot_config={"feature": {"enabled": False, "limit": None}},
            session_config={"feature": {"limit": 0, "label": None}},
            user_config={"feature": {"label": ""}},
            relationship_config={"feature": {"enabled": None}},
            field_metadata={
                "feature.*": {"apply_mode": "hot"},
                "restartable": {"apply_mode": "restart", "restart_required": True},
            },
        )
        self.assertEqual(result.revision, repeated.revision)

    def test_layer_store_is_fully_validated_and_illegal_layer_fails_closed(self):
        from services.config.effective_config import EffectiveConfigError, validate_layer_store

        with self.assertRaises(EffectiveConfigError) as caught:
            validate_layer_store({
                "bot": [{"selector": {"bot_id": "bot-alpha"}, "patch": {"trace_enabled": False}}],
                "global-ish": [],
            })
        self.assertEqual(caught.exception.reason_code, "invalid_config_layer")

        with self.assertRaises(EffectiveConfigError) as caught:
            validate_layer_store({
                "session": [{
                    "selector": {"bot_id": "bot-alpha", "session_id": "test:group:g1"},
                    "patch": {},
                }]
            })
        self.assertEqual(caught.exception.reason_code, "invalid_layer_selector")

    def test_channel_layer_resolution_is_exact_and_preserves_legacy_system_settings(self):
        from services.config.channel_config import resolve_effective_channel_config

        g1 = self._scope(conversation="g1", subject="test:user:u1")
        g2 = self._scope(conversation="g2", subject="test:user:u1")
        config = {
            "Query_Settings": {"inject_top_k": 4, "enable_epa": False},
            "Channel_Settings": {
                "trace_enabled": False,
                "channels": {"memory": {"top_k": 6}},
                "layers": {
                    "bot": [{
                        "selector": {"bot_id": "bot-alpha"},
                        "patch": {"channels": {"memory": {"top_k": 7}}},
                    }],
                    "session": [{
                        "selector": {
                            "bot_id": "bot-alpha",
                            "visibility": "group",
                            "session_id": "test:group:g1",
                        },
                        "patch": {"channels": {"memory": {"top_k": 8}}},
                    }],
                    "user": [{
                        "selector": {
                            "bot_id": "bot-alpha",
                            "visibility": "group",
                            "session_id": "test:group:g1",
                            "subject_principal_id": "test:user:u1",
                        },
                        "patch": {"channels": {"memory": {"top_k": 0}}, "trace_enabled": False},
                    }],
                },
            },
        }

        g1_config, g1_effective = resolve_effective_channel_config(config, scope=g1)
        g2_config, g2_effective = resolve_effective_channel_config(config, scope=g2)

        self.assertEqual(g1_config.channels["memory"].top_k, 0)
        self.assertEqual(g2_config.channels["memory"].top_k, 7)
        self.assertIs(g1_config.trace_enabled, False)
        self.assertIs(g1_config.query_stages["epa"], False)
        self.assertEqual(g1_effective.provenance["channels.memory.top_k"]["layer"], "user")
        self.assertEqual(g2_effective.provenance["channels.memory.top_k"]["layer"], "bot")
        self.assertNotEqual(g1_effective.revision, g2_effective.revision)


if __name__ == "__main__":
    unittest.main()
