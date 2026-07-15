import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class ReviewCandidateToolTest(unittest.TestCase):
    def _stores(self):
        from services.injection.trace_store import InjectionTraceStore
        from services.review.candidate_store import ReviewCandidateStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "review.db")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        conn.execute("INSERT INTO memories (id, content) VALUES (7, '证据记忆')")
        conn.commit()
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        candidate_store = ReviewCandidateStore(conn)
        candidate_store.ensure_schema()
        return conn, trace_store, candidate_store

    def _record_trace(self, trace_store):
        trace_store.record(
            {"trace_id": "trace-review-1", "timestamp": 123, "mode": "full", "message": "苹果派", "status": "ok"},
            [],
        )

    def test_candidate_requires_valid_type_content_evidence_and_reason(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        tool = WaveMemorySubmitReviewCandidateTool(conn=conn, trace_store=trace_store, candidate_store=candidate_store)

        no_evidence = asyncio.run(tool.call(None, type="fact", content="用户 喜欢 苹果派", reason="从对话推断"))
        no_reason = asyncio.run(tool.call(None, type="fact", content="用户 喜欢 苹果派", evidence=["memory:7"]))
        bad_type = asyncio.run(tool.call(None, type="persona", content="危险候选", evidence=["memory:7"], reason="test"))

        self.assertIn("必须提供 evidence", no_evidence)
        self.assertIn("必须提供 reason", no_reason)
        self.assertIn("type 必须是", bad_type)
        self.assertEqual(candidate_store.list_pending(), [])

    def test_valid_candidate_enters_review_queue_without_promotion(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        self._record_trace(trace_store)
        tool = WaveMemorySubmitReviewCandidateTool(conn=conn, trace_store=trace_store, candidate_store=candidate_store)

        payload = json.loads(asyncio.run(tool.call(
            None,
            type="belief",
            content="用户对苹果派有稳定偏好",
            evidence=["trace:trace-review-1", "memory:7"],
            reason="多次对话中出现",
        )))
        rows = candidate_store.list_pending()

        self.assertEqual(payload["status"], "pending_review")
        self.assertEqual(payload["candidate_type"], "belief")
        self.assertFalse(payload["promoted"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["candidate_type"], "belief")
        self.assertEqual(rows[0]["review_status"], "pending")
        self.assertFalse(rows[0]["promoted"])
        self.assertEqual(rows[0]["evidence"], ["trace:trace-review-1", "memory:7"])

    def test_missing_trace_or_memory_evidence_is_rejected(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        tool = WaveMemorySubmitReviewCandidateTool(conn=conn, trace_store=trace_store, candidate_store=candidate_store)

        missing_trace = asyncio.run(tool.call(
            None,
            type="jargon",
            content="苹果派=某个群内暗号",
            evidence=["trace:missing"],
            reason="需要人工确认",
        ))
        missing_memory = asyncio.run(tool.call(
            None,
            type="style",
            content="温和解释，不攻击用户",
            evidence=["memory:999"],
            reason="来自用户反馈",
        ))

        self.assertIn("找不到证据 trace", missing_trace)
        self.assertIn("找不到证据 memory", missing_memory)
        self.assertEqual(candidate_store.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
