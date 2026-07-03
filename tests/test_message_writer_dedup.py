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

    def find_recent_duplicate_memory(self, *, group_id, normalized_content, since_ts):
        self.duplicate_checks.append({
            "group_id": group_id,
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
                "group_id": "group-1",
                "sender_id": "user-1",
                "sender_name": "用户",
                "content": "用户喜欢苹果派",
                "timestamp": 1000.0,
                "source": "core",
                "importance": 1.0,
            },
            {
                "group_id": "group-1",
                "sender_id": "bot_remember",
                "sender_name": "主动记忆",
                "content": "  用户喜欢苹果派  ",
                "timestamp": 1001.0,
                "source": "core",
                "importance": 1.5,
            },
            {
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
        self.assertEqual(writer.stats.get("duplicate"), 2)

    def test_recent_duplicate_is_skipped_without_modifying_existing_memory(self):
        db = FakeDB()
        db.recent_duplicate_id = 42
        writer = self._writer(db, window_seconds=120)

        asyncio.run(writer._process_batch([
            {
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
        self.assertEqual(db.duplicate_checks[0]["group_id"], "group-1")
        self.assertEqual(db.duplicate_checks[0]["normalized_content"], "用户喜欢苹果派")
        self.assertEqual(db.duplicate_checks[0]["since_ts"], 1880.0)
        self.assertEqual(writer.stats.get("duplicate"), 1)


if __name__ == "__main__":
    unittest.main()
