import asyncio
import sqlite3
import time
import unittest


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY, summary TEXT, timestamp REAL, sender_id TEXT,
                content TEXT, bot_id TEXT, session_id TEXT, visibility TEXT,
                resolution_state TEXT, quarantine INTEGER
            )"""
        )

    def add(self, memory_id, summary, *, bot="yushu", session="qq:group:g1", quarantine=0, state="resolved"):
        self.conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, 'u', '用户参与了咖啡话题', ?, ?, 'group', ?, ?)",
            (memory_id, summary, time.time(), bot, session, state, quarantine),
        )
        self.conn.commit()


class TimelineScopeTest(unittest.TestCase):
    def setUp(self):
        self.db = _Db()
        self.addCleanup(self.db.conn.close)
        self.db.add(1, "本 Bot 本会话事件")
        self.db.add(2, "同群另一 Bot", bot="bzz")
        self.db.add(3, "跨会话", session="qq:group:g2")
        self.db.add(4, "隔离事件", quarantine=1)
        self.db.add(5, "legacy 事件", state="unresolved_legacy")

    @staticmethod
    def _scope():
        from domain.scope import RuntimeScope, SessionRef

        return RuntimeScope("yushu", "group", SessionRef("qq:group:g1", "qq", "group", "g1"))

    def _ctx(self, scope):
        from services.injection.context import InjectionContext

        return InjectionContext(
            event=object(), req=object(), message="咖啡", group_id="g1", sender_id="u",
            sender_name="用户", bot_id="yushu", bot_profile_id="yushu", scope=scope,
            config={"channels": {"timeline": {"max_items": 10}}}, now=time.time(),
        )

    def test_timeline_requires_and_applies_full_group_scope(self):
        from services.injection.channels.timeline import TimelineChannel

        result = asyncio.run(TimelineChannel(db=self.db).build(self._ctx(self._scope())))

        self.assertEqual(result.status, "hit")
        self.assertIn("本 Bot 本会话", result.text)
        self.assertNotIn("另一 Bot", result.text)
        self.assertNotIn("跨会话", result.text)
        self.assertNotIn("隔离", result.text)
        self.assertNotIn("legacy", result.text)

    def test_timeline_missing_scope_returns_empty(self):
        from services.injection.channels.timeline import TimelineChannel

        result = asyncio.run(TimelineChannel(db=self.db).build(self._ctx(None)))
        self.assertEqual(result.status, "empty")
        self.assertTrue(any("RuntimeScope" in warning for warning in result.warnings))
