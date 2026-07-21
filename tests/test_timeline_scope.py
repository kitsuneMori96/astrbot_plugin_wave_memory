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
                content TEXT, group_id TEXT, bot_id TEXT, session_id TEXT, visibility TEXT,
                resolution_state TEXT, quarantine INTEGER
            )"""
        )

    def add(self, memory_id, summary, *, bot="yushu", session="qq:group:g1", quarantine=0, state="resolved"):
        self.conn.execute(
            "INSERT INTO memories VALUES (?, ?, ?, 'u', '用户参与了咖啡话题', ?, ?, ?, 'group', ?, ?)",
            (memory_id, summary, time.time(), session.rsplit(":", 1)[-1], bot, session, state, quarantine),
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
        self.db.conn.execute(
            "INSERT INTO memories VALUES (6, 'partial Scope 事件', ?, 'u', '用户参与了咖啡话题', 'g3', 'yushu', NULL, 'group', 'resolved', 0)",
            (time.time(),),
        )
        self.db.conn.execute(
            "INSERT INTO memories VALUES (7, '当前群 legacy resolved', ?, 'u', '用户参与了咖啡话题', 'g1', '', '', '', '', 0)",
            (time.time(),),
        )
        self.db.conn.execute(
            "INSERT INTO memories VALUES (8, '跨群 legacy resolved', ?, 'u', '用户参与了咖啡话题', 'g2', '', '', '', '', 0)",
            (time.time(),),
        )
        self.db.conn.commit()

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
        """Same-group mode: open read inside the group (bot/session optional)."""
        from services.injection.channels.timeline import TimelineChannel

        result = asyncio.run(
            TimelineChannel(db=self.db, cross_group_enabled=False).build(self._ctx(self._scope()))
        )

        self.assertEqual(result.status, "hit")
        self.assertIn("本 Bot 本会话", result.text)
        self.assertIn("当前群 legacy resolved", result.text)
        self.assertIn("另一 Bot", result.text)
        self.assertIn("legacy 事件", result.text)
        self.assertNotIn("跨会话", result.text)
        self.assertNotIn("跨群 legacy resolved", result.text)
        self.assertNotIn("隔离", result.text)

    def test_cross_group_default_includes_other_bots_and_groups_but_not_unsafe_rows(self):
        from services.injection.channels.timeline import TimelineChannel

        result = asyncio.run(TimelineChannel(db=self.db).build(self._ctx(self._scope())))

        self.assertEqual(result.status, "hit")
        self.assertIn("本 Bot 本会话", result.text)
        self.assertIn("同群另一 Bot", result.text)
        self.assertIn("跨会话", result.text)
        self.assertIn("当前群 legacy resolved", result.text)
        self.assertIn("跨群 legacy resolved", result.text)
        self.assertIn("legacy 事件", result.text)
        self.assertIn("partial Scope", result.text)
        self.assertIn("[群", result.text)
        self.assertNotIn("隔离", result.text)

    def test_timeline_missing_or_non_group_scope_returns_empty(self):
        from domain.scope import RuntimeScope, SessionRef
        from services.injection.channels.timeline import TimelineChannel

        missing = asyncio.run(TimelineChannel(db=self.db).build(self._ctx(None)))
        private_scope = RuntimeScope(
            "yushu", "private", SessionRef("qq:private:u", "qq", "private", "u")
        )
        private = asyncio.run(TimelineChannel(db=self.db).build(self._ctx(private_scope)))

        self.assertEqual(missing.status, "empty")
        self.assertEqual(private.status, "empty")
        self.assertTrue(any("RuntimeScope" in warning for warning in missing.warnings))
