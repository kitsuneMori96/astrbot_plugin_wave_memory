import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class ReviewCandidateToolTest(unittest.TestCase):
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
        from services.injection.trace_store import InjectionTraceStore
        from services.review.candidate_store import ReviewCandidateStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "review.db")
        self.addCleanup(conn.close)
        conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                content TEXT,
                bot_id TEXT,
                session_id TEXT,
                visibility TEXT,
                resolution_state TEXT DEFAULT 'resolved',
                quarantine INTEGER DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT INTO memories VALUES (7, '证据记忆', 'bot-alpha', 'test:group:g1', 'group', 'resolved', 0)"
        )
        conn.execute(
            "INSERT INTO memories VALUES (8, '跨 scope 证据', 'bot-beta', 'test:group:g2', 'group', 'resolved', 0)"
        )
        conn.commit()
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        candidate_store = ReviewCandidateStore(conn)
        candidate_store.ensure_schema()
        return conn, trace_store, candidate_store

    def _record_trace(self, trace_store, scope):
        trace_store.record(
            {
                "trace_id": "trace-review-1",
                "timestamp": 123,
                "mode": "full",
                "group_id": scope.session.conversation_id,
                "bot_id": scope.bot_id,
                "metadata": self._metadata(scope),
                "message": "苹果派",
                "status": "ok",
            },
            [],
        )

    def test_candidate_requires_runtime_scope(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        result = asyncio.run(
            WaveMemorySubmitReviewCandidateTool(
                conn=conn,
                trace_store=trace_store,
                candidate_store=candidate_store,
            ).call(
                None,
                type="fact",
                content="用户 喜欢 苹果派",
                evidence=["memory:7"],
                reason="从对话推断",
            )
        )

        self.assertIn("scope_required", result)
        self.assertEqual(candidate_store.list_pending(), [])

    def test_valid_candidate_enters_scope_stamped_review_queue_without_promotion(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemorySubmitReviewCandidateTool(conn=conn, trace_store=trace_store, candidate_store=candidate_store)

        payload = json.loads(asyncio.run(tool.call(
            self._ctx(scope),
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
        self.assertEqual(rows[0]["evidence"], ["trace:trace-review-1", "memory:7"])
        self.assertEqual(rows[0]["metadata"]["source_runtime_scope"]["kind"], "RuntimeScope")

    def test_cross_scope_or_legacy_evidence_is_rejected(self):
        from tools.review_candidate import WaveMemorySubmitReviewCandidateTool

        conn, trace_store, candidate_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemorySubmitReviewCandidateTool(conn=conn, trace_store=trace_store, candidate_store=candidate_store)

        foreign_trace = asyncio.run(tool.call(
            self._ctx(self._scope(bot_id="bot-beta", group_id="g2")),
            type="jargon",
            content="苹果派=某个群内暗号",
            evidence=["trace:trace-review-1"],
            reason="需要人工确认",
        ))
        foreign_memory = asyncio.run(tool.call(
            self._ctx(scope),
            type="style",
            content="温和解释，不攻击用户",
            evidence=["memory:8"],
            reason="来自用户反馈",
        ))
        legacy_trace = asyncio.run(tool.call(
            self._ctx(scope),
            type="fact",
            content="旧 trace 不可用",
            evidence=["trace:missing"],
            reason="测试拒绝",
        ))

        self.assertIn("scope_mismatch", foreign_trace)
        self.assertIn("scope_mismatch", foreign_memory)
        self.assertIn("scope_mismatch", legacy_trace)
        self.assertEqual(candidate_store.list_pending(), [])


if __name__ == "__main__":
    unittest.main()
