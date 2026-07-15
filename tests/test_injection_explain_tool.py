import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class InjectionExplainToolTest(unittest.TestCase):
    def _trace_store(self):
        from services.injection.trace_store import InjectionTraceStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "trace.db")
        self.addCleanup(conn.close)
        store = InjectionTraceStore(conn)
        store.ensure_schema()
        return store

    def test_missing_trace_returns_friendly_error(self):
        from tools.injection_explain import WaveMemoryExplainInjectionTool

        tool = WaveMemoryExplainInjectionTool(trace_store=self._trace_store())
        result = asyncio.run(tool.call(None, trace_id="missing-trace"))

        self.assertIn("没有找到注入 trace", result)
        self.assertIn("missing-trace", result)

    def test_existing_trace_returns_structured_channel_explanation(self):
        from services.injection.channel_base import InjectionResult
        from tools.injection_explain import WaveMemoryExplainInjectionTool

        store = self._trace_store()
        store.record(
            {
                "trace_id": "trace-explain-1",
                "timestamp": 123,
                "mode": "full",
                "group_id": "g1",
                "sender_id": "u1",
                "sender_name": "用户",
                "bot_id": "bot",
                "bot_profile_id": "yushu",
                "message": "苹果派是什么",
                "final_text": "<wave_memory>苹果派记忆</wave_memory>",
                "total_tokens": 9,
                "total_chars": 34,
                "total_latency_ms": 12.5,
                "status": "ok",
            },
            [
                InjectionResult.hit(
                    "memory",
                    "<wave_memory>苹果派记忆</wave_memory>",
                    items=[{"id": 7, "source": "core", "score": 0.91, "preview": "苹果派记忆"}],
                    filtered=[{"id": 8, "filter_reason": "recent_context_duplicate", "preview": "重复记忆"}],
                    latency_ms=7.2,
                ),
                InjectionResult.empty("facts", reason="no safe facts", latency_ms=1.1),
            ],
        )
        tool = WaveMemoryExplainInjectionTool(trace_store=store)

        payload = json.loads(asyncio.run(tool.call(None, trace_id="trace-explain-1")))

        self.assertEqual(payload["trace_id"], "trace-explain-1")
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["budget"]["total_tokens"], 9)
        self.assertEqual(payload["budget"]["total_latency_ms"], 12.5)
        self.assertEqual(payload["channels"][0]["channel"], "memory")
        self.assertEqual(payload["channels"][0]["hit_items"][0]["id"], 7)
        self.assertEqual(payload["channels"][0]["hit_items"][0]["score"], 0.91)
        self.assertEqual(payload["channels"][0]["filtered_items"][0]["reason"], "recent_context_duplicate")
        self.assertEqual(payload["channels"][1]["warnings"], ["no safe facts"])


if __name__ == "__main__":
    unittest.main()
