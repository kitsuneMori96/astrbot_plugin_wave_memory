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
                resolution_state TEXT, quarantine INTEGER,
                origin_fingerprint TEXT DEFAULT '', provenance TEXT DEFAULT ''
            )"""
        )
        self.conn.execute("CREATE VIRTUAL TABLE fts_memories USING fts5(content)")

    def add(self, memory_id, content, *, bot="yushu", session="qq:group:g1", quarantine=0, state="resolved"):
        self.conn.execute(
            "INSERT INTO memories VALUES (?, ?, 'u', '用户', 1, 1, 'live', ?, 'message', ?, ?, 'group', ?, ?, '', '')",
            (memory_id, content, session.rsplit(":", 1)[-1], bot, session, state, quarantine),
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
        self.db.conn.execute(
            "INSERT INTO memories VALUES (6, '咖啡 partial', 'u', '用户', 1, 1, 'live', 'g3', 'message', 'yushu', NULL, 'group', 'resolved', 0, '', '')"
        )
        self.db.conn.execute("INSERT INTO fts_memories(rowid, content) VALUES (6, '咖啡 partial')")
        # Fully-unscoped legacy rows remain an explicit compatibility lane: current
        # group in isolated mode, all group rows in shared mode.
        self.db.conn.execute(
            "INSERT INTO memories VALUES (7, '咖啡 当前群 legacy', 'u', '用户', 1, 1, 'live', 'g1', 'message', '', '', '', '', 0, '', '')"
        )
        self.db.conn.execute("INSERT INTO fts_memories(rowid, content) VALUES (7, '咖啡 当前群 legacy')")
        self.db.conn.execute(
            "INSERT INTO memories VALUES (8, '咖啡 跨群 legacy', 'u', '用户', 1, 1, 'live', 'g2', 'message', '', '', '', '', 0, '', '')"
        )
        self.db.conn.execute("INSERT INTO fts_memories(rowid, content) VALUES (8, '咖啡 跨群 legacy')")
        self.db.conn.execute(
            "INSERT INTO memories VALUES (9, '咖啡 noise', 'u', '用户', 1, 1, 'noise', 'g2', 'message', 'bzz', 'qq:group:g2', 'group', 'resolved', 0, '', '')"
        )
        self.db.conn.execute("INSERT INTO fts_memories(rowid, content) VALUES (9, '咖啡 noise')")
        self.db.conn.commit()

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
        """Same-group mode: any active row in the group (Scope optional)."""
        from services.injection.channels.fts5 import FTS5Channel

        result = asyncio.run(
            FTS5Channel(db=self.db, cross_group_enabled=False).build(self._ctx(self._scope()))
        )

        self.assertEqual(result.status, "hit")
        # g1: formal, other bot, unresolved, fully unscoped — all searchable.
        self.assertEqual({item["id"] for item in result.items}, {1, 2, 5, 7})
        self.assertIn("豆子", result.text)
        self.assertIn("当前群 legacy", result.text)
        self.assertIn("另一 Bot", result.text)
        self.assertNotIn("跨会话", result.text)
        self.assertNotIn("跨群 legacy", result.text)
        self.assertNotIn("已隔离", result.text)
        self.assertNotIn("noise", result.text)

    def test_like_fallback_keeps_the_same_scope_predicate(self):
        from services.injection.channels.fts5 import FTS5Channel

        rows = FTS5Channel(db=self.db, cross_group_enabled=False)._scoped_like_search(
            words=["咖啡"], limit=20, scope=self._scope()
        )
        self.assertEqual({row[0] for row in rows}, {1, 2, 5, 7})

    def test_cross_group_default_includes_other_bots_and_groups_but_not_unsafe_rows(self):
        from services.injection.channels.fts5 import FTS5Channel

        result = asyncio.run(FTS5Channel(db=self.db).build(self._ctx(self._scope())))

        self.assertEqual(result.status, "hit")
        # Active rows across groups, including partial/unresolved; not quarantine/noise.
        self.assertEqual({item["id"] for item in result.items}, {1, 2, 3, 5, 6, 7, 8})
        self.assertIn("[群 g1]", result.text)
        self.assertIn("[群 g2]", result.text)
        self.assertIn("当前群 legacy", result.text)
        self.assertIn("跨群 legacy", result.text)
        self.assertNotIn("已隔离", result.text)
        self.assertNotIn("noise", result.text)

    def test_shared_grant_allows_only_granted_foreign_id_when_cross_group_off(self):
        from services.injection.channels.fts5 import FTS5Channel

        class GrantRepo:
            def active_memory_ids_for_consumer(self, *, consumer_scope, limit=5000):
                return [3]  # g2 formal row only

        self.db.shared_memory_grants = GrantRepo()
        channel = FTS5Channel(
            db=self.db,
            cross_group_enabled=False,
            shared_memory_grants_enabled=True,
        )
        result = asyncio.run(channel.build(self._ctx(self._scope())))
        self.assertEqual(result.status, "hit")
        ids = {item["id"] for item in result.items}
        self.assertIn(1, ids)
        self.assertIn(2, ids)  # same group, other bot — still searchable
        self.assertIn(3, ids)  # granted foreign
        self.assertNotIn(8, ids)  # cross-group legacy not via grant list
        granted = next(item for item in result.items if item["id"] == 3)
        self.assertTrue(granted.get("_shared_grant"))

    def test_shared_grant_disabled_keeps_exact_scope(self):
        from services.injection.channels.fts5 import FTS5Channel

        class GrantRepo:
            def active_memory_ids_for_consumer(self, *, consumer_scope, limit=5000):
                return [3]

        self.db.shared_memory_grants = GrantRepo()
        result = asyncio.run(
            FTS5Channel(
                db=self.db,
                cross_group_enabled=False,
                shared_memory_grants_enabled=False,
            ).build(self._ctx(self._scope()))
        )
        # Same-group open read (not bot/session exact).
        self.assertEqual({item["id"] for item in result.items}, {1, 2, 5, 7})

    def test_cross_group_like_fallback_reuses_the_predicate(self):
        from services.injection.channels.fts5 import FTS5Channel

        rows = FTS5Channel(db=self.db)._scoped_like_search(
            words=["咖啡"], limit=20, scope=self._scope()
        )
        self.assertEqual({row[0] for row in rows}, {1, 2, 3, 5, 6, 7, 8})

    def test_missing_or_non_group_scope_is_empty_not_a_legacy_group_query(self):
        from domain.scope import RuntimeScope, SessionRef
        from services.injection.channels.fts5 import FTS5Channel

        missing = asyncio.run(FTS5Channel(db=self.db).build(self._ctx(None)))
        private_scope = RuntimeScope(
            "yushu", "private", SessionRef("qq:private:u", "qq", "private", "u")
        )
        private = asyncio.run(FTS5Channel(db=self.db).build(self._ctx(private_scope)))

        self.assertEqual(missing.status, "empty")
        self.assertEqual(private.status, "empty")
        self.assertTrue(any("RuntimeScope" in warning for warning in missing.warnings))
