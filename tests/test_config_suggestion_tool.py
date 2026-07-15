import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class ConfigSuggestionToolTest(unittest.TestCase):
    def _stores(self):
        from services.injection.trace_store import InjectionTraceStore
        from services.injection.config_suggestion_store import ConfigSuggestionStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "suggest.db")
        self.addCleanup(conn.close)
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        suggestion_store = ConfigSuggestionStore(conn)
        suggestion_store.ensure_schema()
        return trace_store, suggestion_store

    def _record_trace(self, trace_store, trace_id="trace-suggest-1"):
        trace_store.record(
            {
                "trace_id": trace_id,
                "timestamp": 123,
                "mode": "full",
                "message": "苹果派",
                "final_text": "苹果派记忆",
                "status": "ok",
                "total_latency_ms": 800,
            },
            [],
        )

    def test_suggestion_requires_existing_trace_evidence(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)

        no_evidence = asyncio.run(tool.call(None, scope="channel", channel="memory", problem="slow"))
        self.assertIn("至少提供一个 evidence_trace_id", no_evidence)

        missing = asyncio.run(tool.call(
            None,
            scope="channel",
            channel="memory",
            problem="slow",
            evidence_trace_ids=["missing"],
        ))
        self.assertIn("找不到证据 trace", missing)

    def test_valid_suggestion_is_pending_review_and_queryable(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        self._record_trace(trace_store)
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)

        payload = json.loads(asyncio.run(tool.call(
            None,
            scope="channel",
            channel="memory",
            problem="slow",
            evidence_trace_ids=["trace-suggest-1"],
            suggestion="memory.timeout_ms 可以调高一点，或降低 top_k",
        )))
        rows = suggestion_store.list_pending()

        self.assertEqual(payload["status"], "pending_review")
        self.assertEqual(payload["scope"], "channel")
        self.assertEqual(payload["channel"], "memory")
        self.assertFalse(payload["applied"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["problem"], "slow")
        self.assertEqual(rows[0]["evidence_trace_ids"], ["trace-suggest-1"])
        self.assertEqual(rows[0]["review_status"], "pending")

    def test_invalid_channel_and_problem_are_rejected(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        self._record_trace(trace_store)
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)

        invalid_channel = asyncio.run(tool.call(
            None,
            scope="channel",
            channel="not_a_channel",
            problem="slow",
            evidence_trace_ids=["trace-suggest-1"],
        ))
        invalid_problem = asyncio.run(tool.call(
            None,
            scope="channel",
            channel="memory",
            problem="delete_everything",
            evidence_trace_ids=["trace-suggest-1"],
        ))

        self.assertIn("未知通道", invalid_channel)
        self.assertIn("problem 必须是", invalid_problem)
        self.assertEqual(suggestion_store.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
