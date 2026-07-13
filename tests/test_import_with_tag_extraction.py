import asyncio
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    api_mod.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    astrbot_mod.api = api_mod
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod

from webui.source_discovery import UniversalImporter


class _FakeWaveDB:
    def __init__(self, conn):
        self.conn = conn
        self._next_mem_id = 0
        self._next_tag_id = 100

    def add_memory(self, *, group_id, content, sender_name, vector, timestamp):
        self._next_mem_id += 1
        self.conn.execute(
            "INSERT INTO memories (id, group_id, content, sender_name, timestamp) VALUES (?, ?, ?, ?, ?)",
            (self._next_mem_id, group_id, content, sender_name, timestamp),
        )
        self.conn.commit()
        return self._next_mem_id

    def add_tag_extended(self, *, name, tag_type="keyword", vector=None, confidence=0.8):
        row = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            return row[0]
        self._next_tag_id += 1
        self.conn.execute(
            "INSERT INTO tags (id, name, tag_type, confidence) VALUES (?, ?, ?, ?)",
            (self._next_tag_id, name, tag_type, confidence),
        )
        self.conn.commit()
        return self._next_tag_id


class _FakeEmbedding:
    async def get_embeddings(self, texts):
        return [None for _ in texts]


class _FakeTagExtractor:
    provider_id = "tag-llm-provider"

    def __init__(self):
        self.calls = []

    async def extract_tags_batch(self, messages):
        self.calls.append(messages)
        return [[{"name": f"导入标签{m['id']}", "type": "topic", "confidence": 0.91}] for m in messages]


class ImportWithTagExtractionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.source_path = Path(self.tmp.name) / "source.db"
        source = sqlite3.connect(self.source_path)
        source.execute("CREATE TABLE external_messages (content TEXT, sender TEXT, ts REAL, group_id TEXT)")
        source.execute(
            "INSERT INTO external_messages (content, sender, ts, group_id) VALUES (?, ?, ?, ?)",
            ("这是一条外部导入后需要同步打标签的长记忆", "alice", 123.0, "group-1"),
        )
        source.commit()
        source.close()

        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, group_id TEXT, content TEXT, sender_name TEXT, timestamp REAL)")
        self.conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, tag_type TEXT, confidence REAL)")
        self.conn.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER, position INTEGER, relevance REAL, UNIQUE(memory_id, tag_id))")
        self.conn.execute("CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()

    def test_known_memory_import_without_scope_is_blocked_before_embedding_or_tagging(self):
        db = _FakeWaveDB(self.conn)
        extractor = _FakeTagExtractor()
        importer = UniversalImporter(db, _FakeEmbedding(), tag_extractor=extractor, memory_index=None)
        source = {
            "id": "external-test",
            "name": "External Test",
            "db_path": str(self.source_path),
            "adapter": {
                "table": "external_messages",
                "fields": {
                    "content": "content",
                    "sender": "sender",
                    "timestamp": "ts",
                    "group": "group_id",
                },
                "filter": "LENGTH(content) >= 10",
            },
        }

        events = asyncio.run(_collect_json(importer.import_known(source, limit=10, extract_tags=True, tag_batch_size=5, tag_write_policy="append")))

        self.assertEqual(extractor.calls, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM memory_tags").fetchone()[0], 0)
        self.assertEqual(events[-1]["status"], "blocked")
        self.assertEqual(events[-1]["reason_code"], "unresolved_import_not_supported")


async def _collect_json(generator):
    events = []
    async for raw in generator:
        events.append(json.loads(raw))
    return events


if __name__ == "__main__":
    unittest.main()
