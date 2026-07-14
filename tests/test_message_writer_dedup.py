import asyncio
import logging
import sys
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "astrbot.api" not in sys.modules:
    astrbot_module = types.ModuleType("astrbot")
    api_module = types.ModuleType("astrbot.api")
    api_module.logger = logging.getLogger("astrbot-test")
    sys.modules.setdefault("astrbot", astrbot_module)
    sys.modules["astrbot.api"] = api_module


class FakeDB:
    def __init__(self):
        self.added = []
        self.recent_duplicate_id = None
        self.duplicate_checks = []

    def add_memory(self, **kwargs):
        self.added.append(kwargs)
        return len(self.added)

    def find_recent_duplicate_memory(self, *, scope, normalized_content, since_ts):
        self.duplicate_checks.append({
            "scope": scope,
            "normalized_content": normalized_content,
            "since_ts": since_ts,
        })
        return self.recent_duplicate_id


class FakeIndex:
    def __init__(self):
        self.added = []
        self.saved = False
        self.count = 0

    def add(self, ids, vectors):
        self.added.append({"ids": ids, "vectors": vectors})
        self.count += len(ids)

    def save(self):
        self.saved = True


class FakeEmbedding:
    async def get_embeddings(self, texts):
        return [np.ones(3, dtype=np.float32) * (idx + 1) for idx, _ in enumerate(texts)]


class FakeWriteGateway:
    def __init__(self):
        self.calls = []

    async def append_memory(self, **kwargs):
        self.calls.append(dict(kwargs))
        return len(self.calls)


def _group_scope(*, bot_id="bot-a", group_id="group-1"):
    from astrbot_plugin_wave_memory.domain.scope import RuntimeScope, SessionRef

    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"qq:group:{group_id}",
            platform_id="qq",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id="qq:user:user-1",
    )


class MessageWriterDedupTest(unittest.TestCase):
    def _writer(self, db=None, *, window_seconds=300):
        from astrbot_plugin_wave_memory.services.message_writer import MemoryDedupPolicy, MessageWriter

        return MessageWriter(
            db=db or FakeDB(),
            memory_index=FakeIndex(),
            embedding_service=FakeEmbedding(),
            bot_keywords=set(),
            noise_max_length=1,
            dedup_policy=MemoryDedupPolicy(window_seconds=window_seconds),
        )

    def test_auto_agent_and_compat_writes_share_content_dedup_in_same_batch(self):
        db = FakeDB()
        writer = self._writer(db)

        asyncio.run(writer._process_batch([
            {
                "scope": _group_scope(),
                "group_id": "group-1",
                "sender_id": "user-1",
                "sender_name": "用户",
                "content": "用户喜欢苹果派",
                "timestamp": 1000.0,
                "source": "core",
                "importance": 1.0,
            },
            {
                "scope": _group_scope(),
                "group_id": "group-1",
                "sender_id": "bot_remember",
                "sender_name": "主动记忆",
                "content": "  用户喜欢苹果派  ",
                "timestamp": 1001.0,
                "source": "core",
                "importance": 1.5,
            },
            {
                "scope": _group_scope(),
                "group_id": "group-1",
                "sender_id": "compat_livingmemory",
                "sender_name": "LivingMemory兼容",
                "content": "用户喜欢苹果派",
                "timestamp": 1002.0,
                "source": "compat_livingmemory",
                "importance": 0.7,
            },
        ]))

        self.assertEqual(len(db.added), 1)
        self.assertEqual(db.added[0]["content"], "用户喜欢苹果派")
        self.assertEqual(db.added[0]["importance"], 1.5)
        self.assertEqual(db.added[0]["scope"].bot_id, "bot-a")
        self.assertEqual(db.added[0]["provenance"]["schema"], "memory-origin/v1")
        self.assertEqual(db.added[0]["origin_metadata"]["origin_kind"], "message_writer:core")
        self.assertEqual(writer.stats.get("duplicate"), 2)

    def test_recent_duplicate_is_skipped_without_modifying_existing_memory(self):
        db = FakeDB()
        db.recent_duplicate_id = 42
        writer = self._writer(db, window_seconds=120)

        asyncio.run(writer._process_batch([
            {
                "scope": _group_scope(),
                "group_id": "group-1",
                "sender_id": "bot_remember",
                "sender_name": "主动记忆",
                "content": "用户喜欢苹果派",
                "timestamp": 2000.0,
                "source": "core",
                "importance": 1.5,
            }
        ]))

        self.assertEqual(db.added, [])
        self.assertEqual(db.duplicate_checks[0]["scope"].bot_id, "bot-a")
        self.assertEqual(db.duplicate_checks[0]["scope"].session.id, "qq:group:group-1")
        self.assertEqual(db.duplicate_checks[0]["normalized_content"], "用户喜欢苹果派")
        self.assertEqual(db.duplicate_checks[0]["since_ts"], 1880.0)
        self.assertEqual(writer.stats.get("duplicate"), 1)

    def test_scope_payload_projects_real_group_and_prevents_cross_bot_batch_dedup(self):
        db = FakeDB()
        writer = self._writer(db)

        asyncio.run(writer._process_batch([
            {
                "scope": _group_scope(bot_id="bot-a"),
                "group_id": "group-1",
                "sender_id": "user-1",
                "sender_name": "用户",
                "content": "两个 Bot 不应互相去重",
                "timestamp": 3000.0,
                "source": "core",
            },
            {
                "scope": _group_scope(bot_id="bot-b"),
                "group_id": "group-1",
                "sender_id": "user-1",
                "sender_name": "用户",
                "content": "两个 Bot 不应互相去重",
                "timestamp": 3001.0,
                "source": "core",
            },
        ]))

        self.assertEqual(len(db.added), 2)
        self.assertEqual({item["group_id"] for item in db.added}, {"group-1"})

    def test_gateway_idempotency_hint_is_bound_to_runtime_scope(self):
        from astrbot_plugin_wave_memory.services.message_writer import MessageWriter

        gateway = FakeWriteGateway()
        writer = MessageWriter(
            db=FakeDB(),
            memory_index=FakeIndex(),
            embedding_service=FakeEmbedding(),
            bot_keywords=set(),
            noise_max_length=1,
            write_gateway=gateway,
        )
        base = {
            "group_id": "group-1",
            "sender_id": "user-1",
            "sender_name": "用户",
            "content": "同一平台事件号",
            "timestamp": 3100.0,
            "source": "core",
            "event_id": "event-42",
        }

        asyncio.run(writer._persist_memory(
            {**base, "scope": _group_scope(bot_id="bot-a")},
            vector=np.ones(3), importance=1.0, source="core",
        ))
        asyncio.run(writer._persist_memory(
            {**base, "scope": _group_scope(bot_id="bot-b")},
            vector=np.ones(3), importance=1.0, source="core",
        ))

        self.assertEqual(len(gateway.calls), 2)
        hints = [call["idempotency_hint"] for call in gateway.calls]
        self.assertNotEqual(hints[0], hints[1])
        self.assertIn("bot-a:group:qq:group:group-1:event-42", hints[0])
        self.assertIn("bot-b:group:qq:group:group-1:event-42", hints[1])

    def test_scope_payload_rejects_mismatched_or_non_group_legacy_projection(self):
        from astrbot_plugin_wave_memory.services.message_writer import MessageScopeError
        from astrbot_plugin_wave_memory.domain.scope import RuntimeScope

        writer = self._writer()
        with self.assertRaises(MessageScopeError) as missing:
            asyncio.run(writer._process_batch([{
                "group_id": "group-1",
                "content": "缺少作用域必须拒绝",
            }]))
        self.assertEqual(missing.exception.code, "scope_required")

        with self.assertRaises(MessageScopeError) as mismatch:
            asyncio.run(writer._process_batch([{
                "scope": _group_scope(),
                "group_id": "other-group",
                "content": "作用域不一致",
            }]))
        self.assertEqual(mismatch.exception.code, "scope_session_mismatch")

        private_scope = RuntimeScope(
            bot_id="bot-a",
            visibility="bot_private",
            session=None,
            subject_principal_id=None,
        )
        with self.assertRaises(MessageScopeError) as unsupported:
            asyncio.run(writer._process_batch([{
                "scope": private_scope,
                "content": "不能伪造成群",
            }]))
        self.assertEqual(unsupported.exception.code, "legacy_writer_scope_visibility_unsupported")


if __name__ == "__main__":
    unittest.main()
