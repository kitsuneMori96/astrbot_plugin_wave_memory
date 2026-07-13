import sqlite3
import tempfile
import unittest
from pathlib import Path


class LearningObjectRegistryTest(unittest.TestCase):
    def test_registry_contains_all_required_learning_objects(self):
        from services.learning_objects.registry import get_learning_object_registry

        registry = get_learning_object_registry()

        self.assertEqual(
            set(registry),
            {
                "memory",
                "facts",
                "belief",
                "jargon",
                "few_shot_style",
                "persona_soul_self_experience",
                "affinity",
                "timeline",
                "operation_memory",
            },
        )

    def test_all_learning_objects_have_complete_descriptions(self):
        from services.learning_objects.registry import REQUIRED_FIELDS, VALID_MODES, get_learning_object_registry

        registry = get_learning_object_registry()
        for key, item in registry.items():
            payload = item.to_dict()
            for field in REQUIRED_FIELDS:
                self.assertIn(field, payload, key)
                self.assertTrue(payload[field], f"{key}.{field} must not be empty")
            self.assertTrue(set(item.available_modes).issubset(VALID_MODES), key)
            self.assertIn(item.risk, {"low", "medium", "high"}, key)

    def test_missing_required_field_is_rejected(self):
        from services.learning_objects.registry import LearningObjectDescription, validate_learning_object

        item = LearningObjectDescription(
            key="broken",
            source="test",
            write_path="writer",
            storage_location="db",
            dedup_rule="content hash",
            review_rule="manual",
            recall_path="query",
            injection_channel="",
            safety_filter="identity guard",
            available_modes=("full",),
            webui_visibility="hidden",
            risk="low",
            close_path="test switch",
            audit_findings=("重复写入: test", "无审核: test"),
        )

        with self.assertRaises(ValueError) as ctx:
            validate_learning_object(item)

        self.assertIn("injection_channel", str(ctx.exception))

    def test_registry_is_exportable_for_webui_or_audit(self):
        from services.learning_objects.registry import export_learning_object_registry

        payload = export_learning_object_registry()

        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["key"], "memory")
        self.assertIn("storage_location", payload[0])
        self.assertIn("available_modes", payload[0])

    def test_audit_answers_write_recall_inject_and_close_for_each_object(self):
        from services.learning_objects.registry import get_learning_object_registry

        registry = get_learning_object_registry()
        for key, item in registry.items():
            payload = item.to_dict()
            self.assertTrue(payload["source"], key)
            self.assertTrue(payload["write_path"], key)
            self.assertTrue(payload["recall_path"], key)
            self.assertTrue(payload["injection_channel"], key)
            self.assertTrue(payload["close_path"], key)
            self.assertTrue(payload["audit_findings"], key)
            self.assertIsInstance(payload["audit_findings"], list, key)
            self.assertGreaterEqual(len(payload["audit_findings"]), 2, key)

    def test_audit_findings_mark_required_risk_categories(self):
        from services.learning_objects.registry import export_learning_object_registry

        payload = {item["key"]: item for item in export_learning_object_registry()}
        joined = "\n".join("\n".join(item["audit_findings"]) for item in payload.values())

        for marker in ["重复写入", "无审核", "人格污染", "隐藏注入", "旧版本兼容"]:
            self.assertIn(marker, joined)
        self.assertIn("enable_timeline", payload["timeline"]["close_path"])
        self.assertIn("Jargon_Settings.enabled", payload["jargon"]["close_path"])
        self.assertIn("FewShot_Settings.enabled", payload["few_shot_style"]["close_path"])

    def test_learning_object_review_payload_marks_mode_disabled_and_risk(self):
        from webui.blueprints.learning_object_review import build_learning_object_review_payload

        payload = build_learning_object_review_payload({"Runtime_Settings": {"runtime_mode": "memory_only"}})
        objects = {item["key"]: item for item in payload["objects"]}

        self.assertFalse(objects["belief"]["mode_enabled"])
        self.assertTrue(objects["memory"]["mode_enabled"])
        self.assertGreaterEqual(payload["summary"]["high_risk_objects"], 1)
        self.assertIn("duplicate_entries", payload)
        self.assertTrue(payload["duplicate_entries"])

    def test_learning_object_review_payload_includes_pending_candidates(self):
        from services.review.candidate_store import ReviewCandidateStore
        from webui.blueprints.learning_object_review import build_learning_object_review_payload

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "candidates.db")
        self.addCleanup(conn.close)
        store = ReviewCandidateStore(conn)
        store.create(candidate_type="belief", content="候选信念", evidence=["trace-1"], reason="高风险候选")

        payload = build_learning_object_review_payload({"Runtime_Settings": {"runtime_mode": "full"}}, store)

        self.assertEqual(payload["summary"]["pending_candidates"], 1)
        self.assertEqual(payload["pending_candidates"][0]["content"], "候选信念")
        self.assertTrue(payload["risky_candidates"])

    def test_frontend_routes_learning_object_review_to_learning_center(self):
        page = Path("webui/frontend/src/pages/review/LearningObjectsPage.tsx").read_text(encoding="utf-8")
        api = Path("webui/frontend/src/api/review.ts").read_text(encoding="utf-8")
        routes = Path("webui/frontend/src/app/routes.tsx").read_text(encoding="utf-8")

        self.assertIn("/learning-center", page)
        self.assertNotIn("getLearningObjectsReview", page)
        self.assertNotIn("reviewCandidate", page)
        self.assertNotIn("/api/learning-objects/review", api)
        self.assertNotIn("/learning-objects", routes)


if __name__ == "__main__":
    unittest.main()
