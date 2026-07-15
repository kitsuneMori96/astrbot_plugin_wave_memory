import asyncio
import sys
import types
import unittest


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    api_mod.logger = types.SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None, warning=lambda *a, **k: None)
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


class _Repo:
    def __init__(self):
        self.rows, self.calls = {}, []

    @staticmethod
    def _key(scope):
        return (scope.bot_id, scope.session.id, scope.visibility)

    def upsert_scoped_jargon(self, scope, **record):
        self.calls.append(("upsert", self._key(scope), record["word"]))
        key = (self._key(scope), record["word"])
        previous = self.rows.get(key, {})
        self.rows[key] = {"id": previous.get("id", len(self.rows) + 1), **record}

    def list_scoped_jargon(self, scope, *, status=None, limit, include_archived=False):
        self.calls.append(("list", self._key(scope), status, limit))
        rows = [row for (key, _), row in self.rows.items() if key == self._key(scope)]
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        elif not include_archived:
            rows = [row for row in rows if row.get("status") != "archived"]
        return rows[:limit]


class _Db:
    def __init__(self):
        self.scoped_knowledge = _Repo()


class JargonScopeTest(unittest.TestCase):
    @staticmethod
    def _scope(bot="yushu", conversation="g1"):
        from domain.scope import RuntimeScope, SessionRef
        return RuntimeScope(bot, "group", SessionRef(f"qq:group:{conversation}", "qq", "group", conversation))

    def test_injector_reads_only_current_runtime_scope_and_cache_key(self):
        from services.jargon.inference import JargonInjector
        db, first, second = _Db(), self._scope("yushu", "g1"), self._scope("bzz", "g1")
        db.scoped_knowledge.upsert_scoped_jargon(first, word="本群梗", meaning="只属于 yushu", status="confirmed", is_jargon=True, frequency=1)
        db.scoped_knowledge.upsert_scoped_jargon(second, word="本群梗", meaning="只属于 bzz", status="confirmed", is_jargon=True, frequency=99)
        injector = JargonInjector(db)
        self.assertIn("yushu", injector.get_injection("本群梗", first))
        self.assertIn("bzz", injector.get_injection("本群梗", second))
        self.assertEqual(injector.get_injection("本群梗", None), "")

    def test_service_fails_closed_for_missing_or_non_group_scope(self):
        from domain.scope import RuntimeScope, SessionRef
        from services.jargon.service import JargonService
        db, service = _Db(), JargonService(_Db(), enabled=True, config={"min_messages": 1})
        service.feed_message("猫猫税", None, "u1")
        self.assertFalse(service.should_mine(None))
        private = RuntimeScope("yushu", "private", SessionRef("qq:private:u1", "qq", "private", "u1"))
        self.assertEqual(asyncio.run(service.mine(private)), [])
        self.assertEqual(db.scoped_knowledge.calls, [])

    def test_service_persists_by_scope_repository_not_legacy_table(self):
        from services.jargon.service import JargonService
        db, scope = _Db(), self._scope()
        service = JargonService(db, enabled=True, config={"min_messages": 1, "min_frequency": 1})
        service._filter.get_candidates = lambda *_args, **_kwargs: [{"word": "猫猫税", "frequency": 3, "contexts": []}]
        asyncio.run(service.mine(scope))
        self.assertEqual(db.scoped_knowledge.calls[0][:2], ("list", ("yushu", "qq:group:g1", "group")))
        self.assertEqual(db.scoped_knowledge.calls[1][0], "upsert")

    def test_service_resolves_and_persists_same_scope_memory_anchor(self):
        from services.jargon.service import JargonService

        class _Cursor:
            def __init__(self, rows): self.rows = rows
            def fetchall(self): return list(self.rows)
            def fetchone(self): return self.rows[0] if self.rows else None

        class _Conn:
            def execute(self, sql, params=()):
                if "PRAGMA table_info(memories)" in sql:
                    columns = ["id", "content", "timestamp", "sender_id", "bot_id", "session_id", "visibility", "resolution_state", "quarantine", "memory_type"]
                    return _Cursor([(index, name) for index, name in enumerate(columns)])
                self.last_sql, self.last_params = sql, params
                if "SELECT id FROM memories" in sql:
                    return _Cursor([(42,)])
                return _Cursor([])

        db, scope = _Db(), self._scope()
        db.conn = _Conn()
        service = JargonService(db, enabled=True, config={"min_messages": 1, "min_frequency": 1})
        service._filter.get_candidates = lambda *_args, **_kwargs: [{
            "word": "猫猫税",
            "frequency": 3,
            "contexts": ["交猫猫税"],
            "source_contexts": [{"content": "交猫猫税", "timestamp": 1000.0, "sender_id": "u1"}],
        }]

        asyncio.run(service.mine(scope))

        row = db.scoped_knowledge.list_scoped_jargon(scope, limit=10)[0]
        self.assertEqual(row["source_memory_id"], 42)
        self.assertIn("交猫猫税", row["source_context"])
        self.assertIn("bot_id=?", db.conn.last_sql)
        self.assertIn("qq:group:g1", db.conn.last_params)

    def test_edit_requeues_and_archive_removes_from_default_list(self):
        from services.jargon.service import JargonService

        db, scope = _Db(), self._scope()
        db.scoped_knowledge.upsert_scoped_jargon(
            scope,
            word="猫猫税",
            meaning="旧释义",
            status="confirmed",
            is_jargon=True,
            frequency=3,
            confidence=0.9,
            contexts=["上下文"],
            source_memory_id=42,
            source_context='["上下文"]',
            provenance={"source": "wave_memory"},
        )
        service = JargonService(db)
        item = db.scoped_knowledge.list_scoped_jargon(scope, limit=10)[0]

        updated = service.update_meaning(scope, item["id"], "新释义")
        self.assertEqual(updated["meaning"], "新释义")
        self.assertEqual(updated["status"], "pending")
        service.archive(scope, item["id"])
        self.assertEqual(db.scoped_knowledge.list_scoped_jargon(scope, limit=10), [])
        archived = db.scoped_knowledge.list_scoped_jargon(scope, limit=10, include_archived=True)[0]
        self.assertEqual(archived["status"], "archived")


if __name__ == "__main__":
    unittest.main()
