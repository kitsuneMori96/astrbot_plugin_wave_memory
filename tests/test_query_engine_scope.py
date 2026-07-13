import asyncio
import sys
import types
import unittest

import numpy as np

if "astrbot.api" not in sys.modules:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api


class _Embedding:
    def __init__(self):
        self.calls = 0

    async def get_embedding(self, text):
        self.calls += 1
        return np.array([1.0, 0.0], dtype=np.float32)


class _Index:
    def search(self, vector, k):
        return [(1, 0.1), (2, 0.2), (3, 0.3), (4, 0.4)]


class _ScopedDb:
    def __init__(self):
        self.scopes = []
        self.touched = []

    def get_memories_by_ids(self, ids, *, scope):
        self.scopes.append(scope)
        # Simulates repository-side exact Bot/session filtering of HNSW IDs.
        return [{"id": 1, "content": "本 Bot 本会话", "timestamp": 1, "importance": 1.0}]

    def touch_memories(self, ids):
        self.touched.append(ids)

    def get_memory_vectors(self, ids):
        return {}


class QueryEngineScopeTest(unittest.TestCase):
    @staticmethod
    def _scope(*, bot="yushu", conversation="g1"):
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope(
            bot_id=bot,
            visibility="group",
            session=SessionRef(
                id=f"qq:group:{conversation}",
                platform_id="qq",
                kind="group",
                conversation_id=conversation,
            ),
        )

    def _engine(self):
        from engine.query_engine import QueryEngine

        self.embedding = _Embedding()
        self.db = _ScopedDb()
        return QueryEngine(self.db, _Index(), self.embedding, {"min_similarity": 0.0})

    def test_query_fails_closed_without_or_wrong_scope(self):
        engine = self._engine()
        self.assertEqual(asyncio.run(engine.query("关键词")), [])
        self.assertEqual(self.embedding.calls, 0)

        from domain.scope import RuntimeScope

        private = RuntimeScope(bot_id="yushu", visibility="bot_private", session=None)
        self.assertEqual(asyncio.run(engine.shotgun_query("关键词", scope=private)), [])
        self.assertEqual(self.embedding.calls, 0)
        self.assertEqual(self.db.scopes, [])

    def test_hnsw_candidates_are_post_filtered_by_exact_runtime_scope(self):
        engine = self._engine()
        scope = self._scope(bot="yushu", conversation="g1")

        memories = asyncio.run(engine.query("关键词", group_id="g1", scope=scope))

        self.assertEqual([memory["id"] for memory in memories], [1])
        self.assertEqual(self.db.scopes, [scope])
        self.assertFalse(memories[0]["_is_cross_group"])
        self.assertEqual(self.db.touched, [[1]])

    def test_legacy_repository_signature_fails_closed(self):
        class LegacyDb:
            def get_memories_by_ids(self, ids):
                return [{"id": 1}]

        from engine.query_engine import QueryEngine

        engine = QueryEngine(LegacyDb(), _Index(), _Embedding(), {"min_similarity": 0.0})
        self.assertEqual(asyncio.run(engine.query("关键词", scope=self._scope())), [])


if __name__ == "__main__":
    unittest.main()
