import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class MemoryFeedbackToolTest(unittest.TestCase):
    @staticmethod
    def _scope(*, bot_id="bot-alpha", group_id="g1", subject="u1"):
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope(
            bot_id=bot_id,
            visibility="group",
            session=SessionRef(f"test:group:{group_id}", "test", "group", group_id),
            subject_principal_id=f"test:user:{subject}",
        )

    @staticmethod
    def _ctx(scope):
        return SimpleNamespace(context=SimpleNamespace(event=SimpleNamespace(_wave_memory_runtime_scope=scope)))

    @staticmethod
    def _metadata(scope):
        from domain.scope import ScopeCodec

        return {"runtime_scope": ScopeCodec.to_dict(scope)}

    def _stores(self):
        from services.injection.feedback_store import MemoryFeedbackStore
        from services.injection.trace_store import InjectionTraceStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "feedback.db")
        self.addCleanup(conn.close)
        conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                importance REAL DEFAULT 1.0,
                content TEXT,
                bot_id TEXT,
                session_id TEXT,
                visibility TEXT,
                resolution_state TEXT DEFAULT 'resolved',
                quarantine INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO memories VALUES (7, 1.0, '苹果派记忆', 'bot-alpha', 'test:group:g1', 'group', 'resolved', 0)"
        )
        conn.execute(
            "INSERT INTO memories VALUES (8, 1.2, '其他作用域记忆', 'bot-beta', 'test:group:g2', 'group', 'resolved', 0)"
        )
        conn.commit()
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        feedback_store = MemoryFeedbackStore(conn)
        feedback_store.ensure_schema()
        return conn, trace_store, feedback_store

    def _record_trace(self, trace_store, scope):
        from services.injection.channel_base import InjectionResult

        trace_store.record(
            {
                "trace_id": "trace-feedback-1",
                "timestamp": 123,
                "mode": "full",
                "group_id": scope.session.conversation_id,
                "bot_id": scope.bot_id,
                "metadata": self._metadata(scope),
                "message": "苹果派",
                "final_text": "苹果派记忆",
                "status": "ok",
            },
            [InjectionResult.hit("memory", "苹果派记忆", items=[{"id": 7, "preview": "苹果派记忆"}])],
        )

    def test_feedback_requires_runtime_scope(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        result = asyncio.run(
            WaveMemoryFeedbackMemoryTool(
                trace_store=trace_store,
                feedback_store=feedback_store,
                conn=conn,
            ).call(None, trace_id="missing", memory_id=7, feedback="useful")
        )

        self.assertIn("scope_required", result)

    def test_feedback_requires_same_scope_trace_and_memory(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemoryFeedbackMemoryTool(trace_store=trace_store, feedback_store=feedback_store, conn=conn)

        missing = asyncio.run(tool.call(self._ctx(scope), trace_id="missing", memory_id=7, feedback="useful"))
        foreign_trace = asyncio.run(
            tool.call(self._ctx(self._scope(bot_id="bot-beta", group_id="g2")), trace_id="trace-feedback-1", memory_id=7, feedback="useful")
        )
        foreign_memory = asyncio.run(tool.call(self._ctx(scope), trace_id="trace-feedback-1", memory_id=8, feedback="useful"))

        self.assertIn("scope_mismatch", missing)
        self.assertIn("scope_mismatch", foreign_trace)
        self.assertIn("scope_mismatch", foreign_memory)

    def test_feedback_rejects_memory_not_hit_by_trace(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        # 同 scope 但未命中的 memory，验证 trace evidence 仍不可绕过。
        conn.execute(
            "INSERT INTO memories VALUES (9, 1.0, '同 scope 未命中', 'bot-alpha', 'test:group:g1', 'group', 'resolved', 0)"
        )
        conn.commit()
        result = asyncio.run(
            WaveMemoryFeedbackMemoryTool(trace_store=trace_store, feedback_store=feedback_store, conn=conn).call(
                self._ctx(scope), trace_id="trace-feedback-1", memory_id=9, feedback="useful"
            )
        )

        self.assertIn("不在该 trace 的命中项中", result)

    def test_useful_feedback_records_scope_and_soft_boosts_importance(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemoryFeedbackMemoryTool(
            trace_store=trace_store,
            feedback_store=feedback_store,
            conn=conn,
            auto_apply_useful=True,
        )

        result = json.loads(asyncio.run(tool.call(
            self._ctx(scope),
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="useful",
            reason="这条记忆帮助了回答",
        )))
        rows = feedback_store.list_for_trace("trace-feedback-1")
        importance = conn.execute("SELECT importance FROM memories WHERE id=7").fetchone()[0]

        self.assertEqual(result["status"], "recorded")
        self.assertTrue(result["soft_applied"])
        self.assertEqual(len(rows), 1)
        self.assertGreater(importance, 1.0)
        self.assertEqual(rows[0]["metadata"]["source_runtime_scope"]["kind"], "RuntimeScope")

    def test_misleading_and_duplicate_feedback_do_not_delete_memory(self):
        from tools.memory_feedback import WaveMemoryFeedbackMemoryTool

        conn, trace_store, feedback_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemoryFeedbackMemoryTool(
            trace_store=trace_store,
            feedback_store=feedback_store,
            conn=conn,
            auto_apply_useful=True,
        )

        misleading = json.loads(asyncio.run(tool.call(
            self._ctx(scope),
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="misleading",
            reason="上下文误导",
        )))
        duplicate = json.loads(asyncio.run(tool.call(
            self._ctx(scope),
            trace_id="trace-feedback-1",
            memory_id=7,
            feedback="duplicate",
            reason="和近期上下文重复",
        )))
        memory_exists = conn.execute("SELECT COUNT(*) FROM memories WHERE id=7").fetchone()[0]

        self.assertEqual(misleading["status"], "recorded")
        self.assertFalse(misleading["soft_applied"])
        self.assertEqual(duplicate["feedback"], "duplicate")
        self.assertEqual(memory_exists, 1)


if __name__ == "__main__":
    unittest.main()
