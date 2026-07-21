from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from scripts.retrieval_readiness_readonly import config_checks, _check


class RetrievalReadinessConfigChecksTest(unittest.TestCase):
    def test_config_checks_pass_on_expected_production_shape(self):
        cfg = {
            "Memory_Index_Settings": {
                "hot_max_vectors": 100000,
                "cold_recall_enabled": True,
            },
            "Cross_Group_Settings": {
                "cross_group_enabled": True,
            },
        }
        with mock.patch(
            "scripts.retrieval_readiness_readonly._load_plugin_config",
            return_value=cfg,
        ):
            checks = {c["name"]: c for c in config_checks()}
        self.assertTrue(checks["plugin_config_present"]["ok"])
        self.assertTrue(checks["hot_max_vectors_set"]["ok"])
        self.assertEqual(checks["hot_max_vectors_set"]["detail"], 100000)
        self.assertTrue(checks["cold_recall_enabled"]["ok"])
        self.assertTrue(checks["cross_group_enabled"]["ok"])

    def test_config_checks_fail_when_cold_recall_off(self):
        cfg = {
            "Memory_Index_Settings": {
                "hot_max_vectors": 100000,
                "cold_recall_enabled": False,
            },
            "Cross_Group_Settings": {"cross_group_enabled": True},
        }
        with mock.patch(
            "scripts.retrieval_readiness_readonly._load_plugin_config",
            return_value=cfg,
        ):
            checks = {c["name"]: c for c in config_checks()}
        self.assertFalse(checks["cold_recall_enabled"]["ok"])

    def test_config_checks_missing_config(self):
        with mock.patch(
            "scripts.retrieval_readiness_readonly._load_plugin_config",
            return_value={},
        ):
            checks = config_checks()
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["name"], "plugin_config_present")
        self.assertFalse(checks[0]["ok"])

    def test_check_helper(self):
        row = _check("x", True, {"a": 1})
        self.assertEqual(row, {"name": "x", "ok": True, "detail": {"a": 1}})


if __name__ == "__main__":
    unittest.main()
