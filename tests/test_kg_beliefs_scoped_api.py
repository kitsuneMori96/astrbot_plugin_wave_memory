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

    def test_mutations_require_scope_codec_envelope_and_never_use_legacy(self):
        _, envelope = _scope_envelope()
        self.kg.request = types.SimpleNamespace(get_json=lambda **_: _value({
            "scope": envelope, "subject": "qq:user:1", "predicate": "likes", "object": "猫",
        }))
        fact_payload = asyncio.run(self.kg.create_scoped_fact.__wrapped__())
        self.assertEqual(fact_payload["fact_id"], 11)
        self.assertEqual(self.repo.fact_calls[-1][0].session.id, "qq:group:g1")

        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({
            "scope": envelope, "belief_key": "pet-care", "content": "要温柔对待宠物",
        }))
        belief_payload = asyncio.run(self.beliefs.create_belief.__wrapped__())
        self.assertEqual(belief_payload["belief_id"], 21)
        self.assertEqual(self.repo.belief_calls[-1][0].bot_id, "bot-alpha")

        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({"content": "缺 Scope"}))
        missing_payload, missing_status = asyncio.run(self.beliefs.create_belief.__wrapped__())
        self.assertEqual((missing_payload, missing_status), ({"error": {"code": "scope_required"}}, 400))

    def test_cross_scope_object_is_hidden_as_not_found(self):
        _, envelope = _scope_envelope()
        self.beliefs.request = types.SimpleNamespace(get_json=lambda **_: _value({"scope": envelope}))
        payload, status = asyncio.run(self.beliefs.edit_belief.__wrapped__(999))
        self.assertEqual((payload, status), ({"error": {"code": "scoped_object_not_found"}}, 404))

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

        facts = asyncio.run(self.kg.legacy_audit_facts.__wrapped__())
        beliefs = asyncio.run(self.beliefs.legacy_list_beliefs.__wrapped__())

        for payload in (facts, beliefs):
            self.assertTrue(payload["legacy"])
            self.assertTrue(payload["unresolved_legacy"])
            self.assertIsNone(payload["scope"])
            self.assertTrue(payload["readonly"])
            self.assertEqual(payload["page"]["total"], 1)
            self.assertTrue(payload["items"][0]["legacy"])


if __name__ == "__main__":
    unittest.main()
