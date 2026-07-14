from __future__ import annotations

import asyncio
import types
import unittest


class _ScopedRepo:
    def __init__(self) -> None:
        self.list_calls = []
        self.upsert_calls = []
        self.rows = [
            {
                "id": 7,
                "word": "团建",
                "meaning": "群内活动",
                "status": "confirmed",
                "is_jargon": True,
                "frequency": 3,
                "confidence": 0.9,
                "contexts": [],
                "source_memory_id": None,
                "source_context": None,
                "provenance": {},
                "created_at": 1.0,
                "updated_at": 2.0,
            }
        ]

    def list_scoped_jargon(self, scope, *, status=None, limit=50):
        self.list_calls.append((scope, status, limit))
        return list(self.rows)

    def upsert_scoped_jargon(self, scope, **kwargs):
        self.upsert_calls.append((scope, kwargs))
        return 8


class JargonScopedApiTest(unittest.TestCase):
    @staticmethod
    def _scope_envelope():
        from domain.scope import RuntimeScope, ScopeCodec, SessionRef

        scope = RuntimeScope(
            "bot-alpha",
            "group",
            SessionRef("qq:group:g1", "qq", "group", "g1"),
        )
        return scope, ScopeCodec.to_dict(scope)

    def setUp(self):
        from webui.blueprints import jargon as module

        self.module = module
        self.repo = _ScopedRepo()
        self.old_container = module.get_container
        self.old_request = module.request
        self.old_jsonify = module.jsonify
        module.get_container = lambda: types.SimpleNamespace(
            db=types.SimpleNamespace(scoped_knowledge=self.repo)
        )
        module.jsonify = lambda payload: payload

    def tearDown(self):
        self.module.get_container = self.old_container
        self.module.request = self.old_request
        self.module.jsonify = self.old_jsonify

    def test_list_requires_complete_scope_and_explicit_page(self):
        self.module.request = types.SimpleNamespace(args={})

        payload, status = asyncio.run(self.module.list_jargon.__wrapped__())

        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": {"code": "scope_required"}})
        self.assertEqual(self.repo.list_calls, [])

    def test_list_uses_scoped_repository_and_nested_exact_page(self):
        self.module.request = types.SimpleNamespace(
            args={
                "bot_id": "bot-alpha",
                "session_id": "qq:group:g1",
                "visibility": "group",
                "page": "1",
                "page_size": "10",
            }
        )

        payload = asyncio.run(self.module.list_jargon.__wrapped__())

        self.assertEqual(payload["items"][0]["word"], "团建")
        self.assertEqual(payload["page"], {
            "total": 1,
            "total_status": "exact",
            "reason_code": None,
            "limit": 25,
            "offset": 0,
            "page": 1,
            "page_count": 1,
            "has_more": False,
        })
        self.assertEqual(self.repo.list_calls[0][0].bot_id, "bot-alpha")
        self.assertEqual(self.repo.list_calls[0][0].session.id, "qq:group:g1")

    def test_create_requires_codec_envelope_and_passes_runtime_scope(self):
        _, envelope = self._scope_envelope()
        self.module.request = types.SimpleNamespace(
            get_json=lambda **_kwargs: _async_value(
                {
                    "scope": envelope,
                    "word": "团建",
                    "meaning": "群内活动",
                    "provenance": {"source": "manual"},
                }
            )
        )

        payload, status = asyncio.run(self.module.create_jargon.__wrapped__())

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"]["code"], "anchored_jargon_command_unavailable")
        self.assertEqual(self.repo.upsert_calls, [])

    def test_legacy_jargon_mutations_are_terminally_rejected(self):
        payload, status = asyncio.run(self.module.edit_jargon.__wrapped__(7))

        self.assertEqual((payload, status), ({"error": {"code": "legacy_mutation_disabled"}}, 410))
        self.assertEqual(self.repo.upsert_calls, [])


def _async_value(value):
    async def _result():
        return value

    return _result()


if __name__ == "__main__":
    unittest.main()
