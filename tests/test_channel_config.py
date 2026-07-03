import unittest
from pathlib import Path


class ChannelConfigTest(unittest.TestCase):
    def test_old_query_and_inject_settings_map_to_channel_defaults(self):
        from services.config.channel_config import build_default_channel_config

        config = build_default_channel_config(
            runtime_mode="full",
            query_cfg={"inject_top_k": 7, "min_similarity": "0.42"},
            inject_cfg={"skip_recent_minutes": 45, "facts_max": 4, "timeline_max": 3, "enable_timeline": True},
        )

        self.assertEqual(config.mode, "full")
        self.assertEqual(config.recent_dedup_minutes, 45)
        self.assertEqual(config.channels["memory"].top_k, 7)
        self.assertAlmostEqual(config.channels["memory"].min_score, 0.42)
        self.assertEqual(config.channels["facts"].max_items, 4)
        self.assertEqual(config.channels["timeline"].max_items, 3)
        self.assertTrue(config.channels["timeline"].enabled)
        self.assertTrue(config.channels["safety"].enabled)

    def test_memory_only_disables_advanced_channels_by_default(self):
        from services.config.channel_config import build_default_channel_config

        config = build_default_channel_config(runtime_mode="memory_only")

        self.assertTrue(config.channels["memory"].enabled)
        self.assertTrue(config.channels["safety"].enabled)
        for name in ["persona", "belief", "jargon", "fewshot", "book_lore", "affinity"]:
            self.assertFalse(config.channels[name].enabled, name)
            self.assertNotIn("memory_only", config.channels[name].modes, name)

    def test_valid_hot_overrides_apply_without_losing_old_defaults(self):
        from services.config.channel_config import apply_channel_overrides, build_default_channel_config

        base = build_default_channel_config(runtime_mode="full", query_cfg={"inject_top_k": 5})
        updated = apply_channel_overrides(
            base,
            {
                "channels": {
                    "memory": {"priority": 120, "top_k": 9, "timeout_ms": 250, "min_score": 0.55},
                    "facts": {"enabled": False},
                }
            },
        )

        self.assertEqual(updated.channels["memory"].priority, 120)
        self.assertEqual(updated.channels["memory"].top_k, 9)
        self.assertEqual(updated.channels["memory"].timeout_ms, 250)
        self.assertAlmostEqual(updated.channels["memory"].min_score, 0.55)
        self.assertFalse(updated.channels["facts"].enabled)
        self.assertEqual(updated.channels["timeline"].max_items, base.channels["timeline"].max_items)

    def test_invalid_hot_config_is_rejected(self):
        from services.config.channel_config import apply_channel_overrides, build_default_channel_config

        base = build_default_channel_config(runtime_mode="full")
        bad_overrides = [
            {"channels": {"unknown": {"enabled": True}}},
            {"channels": {"memory": {"token_budget": -1}}},
            {"channels": {"memory": {"timeout_ms": 999999}}},
            {"channels": {"safety": {"enabled": False}}},
            {"channels": {"safety": {"modes": []}}},
            {"channels": {"memory": {"modes": ["full"]}}},
        ]

        for override in bad_overrides:
            with self.assertRaises(ValueError, msg=str(override)):
                apply_channel_overrides(base, override)

    def test_config_exports_webui_payload(self):
        from services.config.channel_config import build_default_channel_config

        payload = build_default_channel_config(runtime_mode="compat_only").to_dict()

        self.assertEqual(payload["mode"], "compat_only")
        self.assertIn("channels", payload)
        self.assertFalse(payload["channels"]["memory"]["enabled"])
        self.assertTrue(payload["channels"]["safety"]["enabled"])

    def test_build_channel_config_from_plugin_config_applies_stored_overrides(self):
        from services.config.channel_config import build_channel_config_from_plugin_config

        config = build_channel_config_from_plugin_config({
            "Runtime_Settings": {"runtime_mode": "full"},
            "Query_Settings": {"inject_top_k": 5},
            "Inject_Settings": {"skip_recent_minutes": 30},
            "Channel_Settings": {"channels": {"memory": {"top_k": 9, "timeout_ms": 250}}},
        })

        self.assertEqual(config.channels["memory"].top_k, 9)
        self.assertEqual(config.channels["memory"].timeout_ms, 250)

    def test_channel_config_diff_reports_changed_fields(self):
        from services.config.channel_config import apply_channel_overrides, build_default_channel_config, channel_config_diff

        base = build_default_channel_config(runtime_mode="full")
        updated = apply_channel_overrides(base, {"channels": {"memory": {"top_k": 8}}, "recent_dedup_minutes": 10})
        diff = channel_config_diff(base, updated)
        paths = {item["path"] for item in diff}

        self.assertIn("channels.memory.top_k", paths)
        self.assertIn("recent_dedup_minutes", paths)

    def test_channel_config_api_helpers_validate_and_preview_without_applying(self):
        from services.config.channel_config import build_default_channel_config
        from webui.blueprints.channel_config import build_channel_config_payload, validate_channel_config_patch

        current = build_default_channel_config(runtime_mode="full")
        payload = build_channel_config_payload({}, current)
        preview = validate_channel_config_patch({}, {"channels": {"memory": {"top_k": 11}}}, current)
        invalid = validate_channel_config_patch({}, {"channels": {"safety": {"enabled": False}}}, current)

        self.assertEqual(payload["current"]["channels"]["memory"]["top_k"], 5)
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["candidate"]["channels"]["memory"]["top_k"], 11)
        self.assertFalse(invalid["ok"])
        self.assertIn("safety channel cannot be disabled", invalid["errors"][0])

    def test_frontend_exposes_channel_config_page(self):
        page = Path("webui/frontend/src/pages/channels/ChannelConfigPage.tsx").read_text(encoding="utf-8")
        table = Path("webui/frontend/src/pages/channels/ChannelConfigTable.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/channels.ts").read_text(encoding="utf-8")
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")

        self.assertIn("Channel Config", page)
        self.assertIn("validateChannelConfig", page)
        self.assertIn("applyChannelConfig", page)
        self.assertIn("resetChannelConfigDefaults", page)
        self.assertIn("safety channel", page)
        self.assertIn("disabled={safety}", table)
        self.assertIn("/api/config/channels", api)
        self.assertIn("/channels", routes)


if __name__ == "__main__":
    unittest.main()

