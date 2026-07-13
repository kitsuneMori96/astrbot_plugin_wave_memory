import asyncio
import sqlite3
import unittest


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY, content TEXT, sender_id TEXT, sender_name TEXT,
                timestamp REAL, importance REAL, source TEXT, group_id TEXT,
                memory_type TEXT, bot_id TEXT, session_id TEXT, visibility TEXT,
                resolution_state TEXT, quarantine INTEGER
            )"""
        )
        self.conn.execute("CREATE VIRTUAL TABLE fts_memories USING fts5(content)")

    def add(self, memory_id, content, *, bot="yushu", session="qq:group:g1", quarantine=0, state="resolved"):
        self.conn.execute(
            "INSERT INTO memories VALUES (?, ?, 'u', '用户', 1, 1, 'live', 'g1', 'message', ?, ?, 'group', ?, ?)",
            (memory_id, content, bot, session, state, quarantine),
        )
        self.conn.execute("INSERT INTO fts_memories(rowid, content) VALUES (?, ?)", (memory_id, content))
        self.conn.commit()


class FTS5ChannelScopeTest(unittest.TestCase):
    def setUp(self):
        self.db = _Db()
        self.addCleanup(self.db.conn.close)
        self.db.add(1, "咖啡 豆子")
        self.db.add(2, "咖啡 同群另一 Bot", bot="bzz")
        self.db.add(3, "咖啡 跨会话", session="qq:group:g2")
        self.db.add(4, "咖啡 已隔离", quarantine=1)
        self.db.add(5, "咖啡 legacy", state="unresolved_legacy")

    @staticmethod
    def _scope():
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope(
            bot_id="yushu",
            visibility="group",
            session=SessionRef("qq:group:g1", "qq", "group", "g1"),
        )

    def _ctx(self, scope):
        from services.injection.context import InjectionContext

        return InjectionContext(
            event=object(), req=object(), message="咖啡", group_id="g1", sender_id="u",
            sender_name="用户", bot_id="yushu", bot_profile_id="yushu", scope=scope,
            config={"channels": {"fts5": {"top_k": 10}}},
        )

    def test_fts_join_filters_bot_session_resolution_and_quarantine(self):
        from services.injection.channels.fts5 import FTS5Channel

        result = asyncio.run(FTS5Channel(db=self.db).build(self._ctx(self._scope())))

        self.assertEqual(result.status, "hit")
        self.assertEqual([item["id"] for item in result.items], [1])
        self.assertIn("豆子", result.text)
        self.assertNotIn("另一 Bot", result.text)
        self.assertNotIn("跨会话", result.text)
        self.assertNotIn("已隔离", result.text)
        self.assertNotIn("legacy", result.text)

    def test_like_fallback_keeps_the_same_scope_predicate(self):
        from services.injection.channels.fts5 import FTS5Channel

        rows = FTS5Channel(db=self.db)._like_fallback(words=["咖啡"], limit=20, scope=self._scope())
        self.assertEqual([row[0] for row in rows], [1])

    def test_missing_scope_is_empty_not_a_legacy_group_query(self):
        from services.injection.channels.fts5 import FTS5Channel

        result = asyncio.run(FTS5Channel(db=self.db).build(self._ctx(None)))
        self.assertEqual(result.status, "empty")
        self.assertTrue(any("RuntimeScope" in warning for warning in result.warnings))
