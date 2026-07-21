"""Timeline channel: shared grants narrow expansion when cross_group is off."""

from __future__ import annotations

import asyncio
import sqlite3
import unittest


class TimelineSharedGrantTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            """CREATE TABLE memories (
                id INTEGER PRIMARY KEY, summary TEXT, content TEXT, sender_id TEXT,
                timestamp REAL, group_id TEXT, bot_id TEXT, session_id TEXT,
                visibility TEXT, resolution_state TEXT, quarantine INTEGER,
                memory_type TEXT, source TEXT
            )"""
        )
        # local g1
        self.conn.execute(
            "INSERT INTO memories VALUES (1,'本地事','本地 content','u',10.0,'g1','yushu','qq:group:g1','group','resolved',0,'message','live')"
        )
        # foreign g2 granted
        self.conn.execute(
            "INSERT INTO memories VALUES (2,'外群授权','外群 content','u',11.0,'g2','yushu','qq:group:g2','group','resolved',0,'message','live')"
        )
        # foreign g3 not granted
        self.conn.execute(
            "INSERT INTO memories VALUES (3,'外群未授权','外群2','u',12.0,'g3','yushu','qq:group:g3','group','resolved',0,'message','live')"
        )
        self.conn.commit()

        class Db:
            def __init__(self, conn):
                self.conn = conn
                self.shared_memory_grants = self

            def active_memory_ids_for_consumer(self, *, consumer_scope, limit=5000):
                return [2]

        self.db = Db(self.conn)

    def tearDown(self):
        self.conn.close()

    def _ctx(self):
        from domain.scope import RuntimeScope, SessionRef
        from services.injection.context import InjectionContext

        scope = RuntimeScope(
            bot_id="yushu",
            visibility="group",
            session=SessionRef("qq:group:g1", "qq", "group", "g1"),
        )
        return InjectionContext(
            event=object(),
            req=object(),
            message="hi",
            group_id="g1",
            sender_id="u",
            sender_name="用户",
            bot_id="yushu",
            bot_profile_id="yushu",
            scope=scope,
            config={"channels": {"timeline": {"max_items": 10, "days": 0}}},
        )

    def test_grant_includes_foreign_summary(self):
        from services.injection.channels.timeline import TimelineChannel

        ch = TimelineChannel(
            db=self.db,
            cross_group_enabled=False,
            shared_memory_grants_enabled=True,
        )
        result = asyncio.run(ch.build(self._ctx()))
        self.assertEqual(result.status, "hit")
        text = result.text or ""
        self.assertIn("本地事", text)
        self.assertIn("外群授权", text)
        self.assertNotIn("外群未授权", text)

    def test_grant_off_excludes_foreign(self):
        from services.injection.channels.timeline import TimelineChannel

        ch = TimelineChannel(
            db=self.db,
            cross_group_enabled=False,
            shared_memory_grants_enabled=False,
        )
        result = asyncio.run(ch.build(self._ctx()))
        text = result.text or ""
        self.assertIn("本地事", text)
        self.assertNotIn("外群授权", text)


if __name__ == "__main__":
    unittest.main()
