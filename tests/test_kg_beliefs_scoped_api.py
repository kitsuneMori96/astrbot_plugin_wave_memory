from __future__ import annotations

import asyncio
import sqlite3
import types
import unittest


class _ScopedRepo:
    def __init__(self) -> None:
        self.fact_rows = [{
            "id": 11, "subject": "qq:user:1", "predicate": "likes", "object": "猫",
            "confidence": 0.8, "status": "reviewed", "source_memory_id": None,
            "provenance": {}, "valid_from": None, "valid_until": None,
            "created_at": 1.0, "updated_at": 2.0,
        }]
        self.belief_rows = [{
            "id": 21, "belief_key": "pet-care", "content": "要温柔对待宠物",
            "belief_type": "world_view", "strength": 0.8, "status": "pending",
            "source_memory_id": None, "provenance": {}, "created_at": 1.0, "updated_at": 2.0,
        }]
        self.fact_calls = []
        self.belief_calls = []

    def list_scoped_facts(self, scope, *, subject=None, limit=50):
        self.fact_calls.append((scope, subject, limit))
        return list(self.fact_rows)

    def upsert_scoped_fact(self, scope, **kwargs):
        self.fact_calls.append((scope, kwargs))
        return 11

    def list_scoped_beliefs(self, scope, *, status=None, limit=50):
        self.belief_calls.append((scope, status, limit))
        return list(self.belief_rows)

    def upsert_scoped_belief(self, scope, **kwargs):
        self.belief_calls.append((scope, kwargs))
        return 21


async def _value(value):
    return value


def _scope_envelope():
    from domain.scope import RuntimeScope, ScopeCodec, SessionRef

    scope = RuntimeScope(
        "bot-alpha", "group", SessionRef("qq:group:g1", "qq", "group", "g1"),
    )
    return scope, ScopeCodec.to_dict(scope)


class ScopedBlueprintApiTest(unittest.TestCase):
    def setUp(self):
        from webui.blueprints import beliefs, kg

        self.beliefs = beliefs
        self.kg = kg
        self.repo = _ScopedRepo()
        self.originals = []
        for module in (beliefs, kg):
            self.originals.append((module, module.get_container, module.request, module.jsonify))
            module.get_container = lambda: types.SimpleNamespace(
                db=types.SimpleNamespace(scoped_knowledge=self.repo)
            )
            module.jsonify = lambda payload: payload

    def tearDown(self):
        for module, get_container, request, jsonify in self.originals:
            module.get_container = get_container
            module.request = request
            module.jsonify = jsonify

    def test_formal_lists_require_complete_scope_and_pagination(self):
        self.kg.request = types.SimpleNamespace(args={})
        kg_payload, kg_status = asyncio.run(self.kg.list_scoped_facts.__wrapped__())
        self.assertEqual((kg_payload, kg_status), ({"error": {"code": "scope_required"}}, 400))

        self.beliefs.request = types.SimpleNamespace(args={})
        belief_payload, belief_status = asyncio.run(self.beliefs.list_beliefs.__wrapped__())
        self.assertEqual((belief_payload, belief_status), ({"error": {"code": "scope_required"}}, 400))
        self.assertEqual(self.repo.fact_calls, [])
        self.assertEqual(self.repo.belief_calls, [])

    def test_formal_lists_use_scoped_repository_and_exact_page_model(self):
        args = {
            "bot_id": "bot-alpha", "session_id": "qq:group:g1", "visibility": "group",
            "page": "1", "page_size": "10",
        }
        self.kg.request = types.SimpleNamespace(args=args)
        kg_payload = asyncio.run(self.kg.list_scoped_facts.__wrapped__())
        self.assertEqual(kg_payload["items"][0]["object"], "猫")
        self.assertEqual(kg_payload["page"]["total"], 1)
        self.assertEqual(self.repo.fact_calls[0][0].bot_id, "bot-alpha")

        self.beliefs.request = types.SimpleNamespace(args=args)
        belief_payload = asyncio.run(self.beliefs.list_beliefs.__wrapped__())
        self.assertEqual(belief_payload["items"][0]["belief_key"], "pet-care")
        self.assertEqual(belief_payload["items"][0]["type"], "world_view")
        self.assertEqual(belief_payload["scope"]["kind"], "RuntimeScope")
        self.assertTrue(belief_payload["capabilities"]["batch_lifecycle"]["available"])
        self.assertTrue(belief_payload["capabilities"]["evidence"]["available"])

    def test_scoped_belief_evidence_restores_only_same_scope_messages(self):
        scope, _ = _scope_envelope()

        class _Cursor:
            def __init__(self, rows=None):
                self.rows = rows or []
            def fetchall(self):
                return list(self.rows)
            def fetchone(self):
                return self.rows[0] if self.rows else None

        class _Conn:
            def __init__(self):
                self.calls = []
            def execute(self, sql, params=()):
                self.calls.append((sql, params))
                if "PRAGMA table_info(memories)" in sql:
                    columns = ["id", "group_id", "sender_id", "sender_name", "content", "timestamp", "bot_id", "session_id", "visibility", "resolution_state", "quarantine", "memory_type"]
                    return _Cursor([(index, name) for index, name in enumerate(columns)])
                assert "bot_id=?" in sql and "session_id=?" in sql and "visibility=?" in sql
                assert "bot-alpha" in params and "qq:group:g1" in params and "group" in params
                if "WHERE id=?" in sql:
                    return _Cursor([(31, "g1", "u1", "用户甲", "要温柔对待宠物", 1000.0)])
                if "timestamp <" in sql:
                    return _Cursor([(30, "g1", "u2", "用户乙", "前文", 990.0)])
                if "timestamp >" in sql:
                    return _Cursor([(32, "g1", "u3", "用户丙", "后文", 1010.0)])
                return _Cursor([])

        payload = self.beliefs._scoped_belief_evidence(
            types.SimpleNamespace(db=types.SimpleNamespace(conn=_Conn())),
            scope,
            {"id": 21, "content": "要温柔对待宠物", "type": "world_view", "revision": 2, "source_memory_id": 31},
            before=1,
            after=1,
        )

        self.assertFalse(payload["used_fallback"])
        self.assertEqual([message["role"] for message in payload["messages"]], ["before", "anchor", "after"])
        self.assertEqual(payload["anchor"]["content"], "要温柔对待宠物")
        self.assertEqual(payload["relationship_events"], [])
        self.assertEqual(payload["episodes"], [])

    def test_scoped_belief_evidence_missing_object_ref_is_rejected(self):
        self.beliefs.request = types.SimpleNamespace(args={
            "bot_id": "bot-alpha",
            "session_id": "qq:group:g1",
            "visibility": "group",
            "before": "5",
            "after": "5",
        })

        payload, status = asyncio.run(self.beliefs.get_scoped_belief_evidence.__wrapped__(21))

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "object_ref_required")

    def test_scoped_batch_lifecycle_validates_object_refs_before_writes(self):
        _, envelope = _scope_envelope()
        validated = []
        original_require = self.beliefs._require_object_ref
        self.beliefs._require_object_ref = lambda body, **kwargs: validated.append((body["object_ref"]["ref"], kwargs["locator"]))
        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_kwargs: _value({
            "scope": envelope,
            "action": "archive",
            "items": [{"id": 21, "object_ref": {"ref": "opaque-21"}, "revision": 2000}],
        }))
        try:
            payload = asyncio.run(self.beliefs.batch_transition_scoped_beliefs.__wrapped__())
        finally:
            self.beliefs._require_object_ref = original_require

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transitioned_count"], 1)
        self.assertEqual(payload["items"], [{"id": 21, "status": "archived"}])
        self.assertEqual(validated, [("opaque-21", 21)])
        self.assertTrue(any(isinstance(call[1], dict) and call[1].get("status") == "archived" for call in self.repo.belief_calls))

    def test_scoped_batch_lifecycle_rejects_one_bad_ref_before_any_write(self):
        _, envelope = _scope_envelope()
        self.repo.belief_rows.append({
            **self.repo.belief_rows[0],
            "id": 22,
            "belief_key": "second",
        })
        original_require = self.beliefs._require_object_ref
        def require_ref(body, **kwargs):
            if kwargs["locator"] == 22:
                raise ValueError("object_ref_stale")
        self.beliefs._require_object_ref = require_ref
        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_kwargs: _value({
            "scope": envelope,
            "action": "archive",
            "items": [
                {"id": 21, "object_ref": {"ref": "opaque-21"}, "revision": 2000},
                {"id": 22, "object_ref": {"ref": "opaque-22"}, "revision": 2000},
            ],
        }))
        try:
            _payload, status = asyncio.run(self.beliefs.batch_transition_scoped_beliefs.__wrapped__())
        finally:
            self.beliefs._require_object_ref = original_require

        self.assertEqual(status, 422)
        self.assertFalse(any(isinstance(call[1], dict) for call in self.repo.belief_calls))

    def test_kg_mutations_are_read_only_while_beliefs_fail_closed(self):
        _, envelope = _scope_envelope()
        self.kg.request = types.SimpleNamespace(get_json=lambda **_: _value({
            "scope": envelope, "subject": "qq:user:1", "predicate": "likes", "object": "猫",
        }))
        fact_payload, fact_status = asyncio.run(self.kg.create_scoped_fact.__wrapped__())
        self.assertEqual((fact_payload, fact_status), ({"error": {"code": "legacy_mutation_disabled"}}, 410))
        self.assertEqual(self.repo.fact_calls, [])

        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({
            "scope": envelope, "belief_key": "pet-care", "content": "要温柔对待宠物",
        }))
        belief_payload, belief_status = asyncio.run(self.beliefs.create_belief.__wrapped__())
        self.assertEqual(belief_status, 503)
        self.assertEqual(belief_payload["error"]["code"], "anchored_belief_command_unavailable")
        self.assertEqual(self.repo.belief_calls, [])

        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({"content": "缺 Scope"}))
        missing_payload, missing_status = asyncio.run(self.beliefs.create_belief.__wrapped__())
        self.assertEqual((missing_payload, missing_status), ({"error": {"code": "anchored_belief_command_unavailable"}}, 503))

    def test_cross_scope_object_is_hidden_as_not_found(self):
        _, envelope = _scope_envelope()
        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({"scope": envelope}))
        payload, status = asyncio.run(self.beliefs.edit_belief.__wrapped__(999))
        self.assertEqual((payload, status), ({"error": {"code": "belief_edit_command_unavailable"}}, 503))

    def test_legacy_kg_and_belief_mutations_are_terminally_rejected(self):
        payload, status = asyncio.run(self.kg.update_tag.__wrapped__(9))
        self.assertEqual((payload, status), ({"error": {"code": "legacy_mutation_disabled"}}, 410))

        payload, status = asyncio.run(self.kg.rename_entity.__wrapped__())
        self.assertEqual((payload, status), ({"error": {"code": "legacy_mutation_disabled"}}, 410))

        payload, status = asyncio.run(self.beliefs.batch_archive.__wrapped__())
        self.assertEqual((payload, status), ({"error": {"code": "legacy_mutation_disabled"}}, 410))

    def test_legacy_audit_routes_label_unresolved_rows_and_paginate(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript("""
            CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, confidence REAL, created_at REAL);
            INSERT INTO facts VALUES (1, '旧用户', '喜欢', '旧物', 0.7, 1.0);
            CREATE TABLE beliefs (
                id INTEGER PRIMARY KEY, content TEXT, type TEXT, strength REAL, bot_id TEXT, sources TEXT,
                status TEXT, created_at REAL, last_reinforced REAL, evidence_type TEXT, evidence_ids TEXT, archived_reason TEXT
            );
            INSERT INTO beliefs VALUES (2, '旧信念内容', 'world', 0.6, 'legacy-bot', '[]', 'pending', 1.0, 1.0, 'memory', '[]', '');
        """)
        legacy_container = lambda: types.SimpleNamespace(db=types.SimpleNamespace(conn=conn))
        self.kg.get_container = legacy_container
        self.beliefs.get_container = legacy_container
        self.kg.request = types.SimpleNamespace(args={"page": "1", "page_size": "10"})
        self.beliefs.request = types.SimpleNamespace(args={"page": "1", "page_size": "10"})

        facts_payload, facts_status = asyncio.run(self.kg.legacy_audit_facts.__wrapped__())
        beliefs = asyncio.run(self.beliefs.legacy_list_beliefs.__wrapped__())

        self.assertEqual(facts_status, 410)
        self.assertEqual(facts_payload["error"]["code"], "legacy_mutation_disabled")
        self.assertTrue(beliefs["legacy"])
        self.assertTrue(beliefs["unresolved_legacy"])
        self.assertIsNone(beliefs["scope"])
        self.assertTrue(beliefs["readonly"])
        self.assertEqual(beliefs["page"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
