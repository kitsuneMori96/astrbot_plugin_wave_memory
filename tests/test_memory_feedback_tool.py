import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


class MemoryFeedbackToolTest(unittest.TestCase):
    def _stores(self):
        from services.injection.trace_store import InjectionTraceStore
        from services.injection.feedback_store import MemoryFeedbackStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "feedback.db")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, importance REAL DEFAULT 1.0, content TEXT)")
        conn.execute("INSERT INTO memories (id, importance, content) VALUES (7, 1.0, '苹果派记忆')")
        conn.execute("INSERT INTO memories (id, importance, content) VALUES (8, 1.2, '错误记忆')")
        conn.commit()
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        feedback_store = MemoryFeedbackStore(conn)
        feedback_store.ensure_schema()
        return conn, trace_store, feedback_store

    def _record_trace(self, trace_store):
        from services.injection.channel_base import InjectionResult

        trace_store.record(
            {
                "trace_id": "trace-feedback-1",
                "timestamp": 123,
                "mode": "full",
                "message": "苹果派",
                "final_text": "苹果派记忆",
                "status": "ok",
            },
            [InjectionResult.hit("memory", "苹果派记忆", items=[{"id": 7, "preview": "苹果派记忆"}])],
        )

    def test_feedback_requires_trace_and_injected_memory_evidence(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        tool = WaveMemoryFeedbackMemoryTool(
            trace_store=trace_store,
            feedback_store=feedback_store,
            conn=conn,
        )

        missing = asyncio.run(tool.call(None, trace_id="missing", memory_id=7, feedback="useful"))
        self.assertIn("没有找到注入 trace", missing)

        self._record_trace(trace_store)
        not_in_trace = asyncio.run(tool.call(None, trace_id="trace-feedback-1", memory_id=8, feedback="useful"))
        self.assertIn("不在该 trace 的命中项中", not_in_trace)

    def test_useful_feedback_records_and_soft_boosts_importance(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        self._record_trace(trace_store)
        tool = WaveMemoryFeedbackMemoryTool(
            trace_store=trace_store,
            feedback_store=feedback_store,
            conn=conn,
            auto_apply_useful=True,
        )

        result = json.loads(asyncio.run(tool.call(
            None,
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="useful",
            reason="这条记忆帮助了回答",
        )))
        rows = feedback_store.list_for_trace("trace-feedback-1")
        importance = conn.execute("SELECT importance FROM memories WHERE id=7").fetchone()[0]

        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["feedback"], "useful")
        self.assertTrue(result["soft_applied"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feedback"], "useful")
        self.assertGreater(importance, 1.0)

    def test_misleading_and_duplicate_feedback_do_not_delete_memory(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        self._record_trace(trace_store)
        tool = WaveMemoryFeedbackMemoryTool(
            trace_store=trace_store,
            feedback_store=feedback_store,
            conn=conn,
            auto_apply_useful=True,
        )

        misleading = json.loads(asyncio.run(tool.call(
            None,
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="misleading",
            reason="上下文误导",
        )))
        duplicate = json.loads(asyncio.run(tool.call(
            None,
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="duplicate",
            reason="和近期上下文重复",
        )))
        memory_exists = conn.execute("SELECT COUNT(*) FROM memories WHERE id=7").fetchone()[0]
        rows = feedback_store.list_for_trace("trace-feedback-1")

        self.assertEqual(misleading["status"], "recorded")
        self.assertFalse(misleading["soft_applied"])
        self.assertEqual(duplicate["feedback"], "duplicate")
        self.assertEqual(memory_exists, 1)
        self.assertEqual([row["feedback"] for row in rows], ["misleading", "duplicate"])


if __name__ == "__main__":
    unittest.main()
