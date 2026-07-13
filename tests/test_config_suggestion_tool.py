import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


class ConfigSuggestionToolTest(unittest.TestCase):
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
        from services.injection.config_suggestion_store import ConfigSuggestionStore
        from services.injection.trace_store import InjectionTraceStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = sqlite3.connect(Path(tmp.name) / "suggest.db")
        self.addCleanup(conn.close)
        trace_store = InjectionTraceStore(conn)
        trace_store.ensure_schema()
        suggestion_store = ConfigSuggestionStore(conn)
        suggestion_store.ensure_schema()
        return trace_store, suggestion_store

    def _record_trace(self, trace_store, scope, trace_id="trace-suggest-1"):
        trace_store.record(
            {
                "trace_id": trace_id,
                "timestamp": 123,
                "mode": "full",
                "group_id": scope.session.conversation_id,
                "bot_id": scope.bot_id,
                "metadata": self._metadata(scope),
                "message": "苹果派",
                "final_text": "苹果派记忆",
                "status": "ok",
                "total_latency_ms": 800,
            },
            [],
        )

    def test_suggestion_requires_runtime_scope_and_same_scope_trace(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)
        missing_scope = asyncio.run(tool.call(
            None,
            scope="channel",
            channel="memory",
            problem="slow",
            evidence_trace_ids=["missing"],
        ))
        scope = self._scope()
        missing_trace = asyncio.run(tool.call(
            self._ctx(scope),
            scope="channel",
            channel="memory",
            problem="slow",
            evidence_trace_ids=["missing"],
        ))
        self._record_trace(trace_store, scope)
        foreign_trace = asyncio.run(tool.call(
            self._ctx(self._scope(bot_id="bot-beta", group_id="g2")),
            scope="channel",
            channel="memory",
            problem="slow",
            evidence_trace_ids=["trace-suggest-1"],
        ))

        self.assertIn("scope_required", missing_scope)
        self.assertIn("scope_mismatch", missing_trace)
        self.assertIn("scope_mismatch", foreign_trace)

    def test_valid_suggestion_is_scope_stamped_pending_review(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)

        payload = json.loads(asyncio.run(tool.call(
            self._ctx(scope),
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
        self.assertEqual(rows[0]["metadata"]["source_runtime_scope"]["kind"], "RuntimeScope")

    def test_input_validation_runs_after_scope_gate(self):
        from tools.config_suggestion import WaveMemorySuggestConfigTool

        trace_store, suggestion_store = self._stores()
        scope = self._scope()
        self._record_trace(trace_store, scope)
        tool = WaveMemorySuggestConfigTool(trace_store=trace_store, suggestion_store=suggestion_store)

        invalid_channel = asyncio.run(tool.call(
            self._ctx(scope),
            scope="channel",
            channel="not_a_channel",
            problem="slow",
            evidence_trace_ids=["trace-suggest-1"],
        ))
        invalid_problem = asyncio.run(tool.call(
            self._ctx(scope),
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
