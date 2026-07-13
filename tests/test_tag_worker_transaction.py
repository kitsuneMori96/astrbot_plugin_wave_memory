import asyncio
import sqlite3
import sys
import types
from types import SimpleNamespace

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from domain.scope import RuntimeScope, SessionRef
from services.tag_worker import TagWorkItem, TagWorker


class _Extractor:
    async def extract_tags_batch(self, messages):
        return [[{"name": "会触发外键错误", "type": "topic", "confidence": 0.9}] for _ in messages]


class _Embedding:
    async def get_embedding(self, name):
        return None


class _DB:
    def __init__(self, connection):
        self.conn = connection

    def upsert_scoped_tag(self, scope, **kwargs):
        cursor = self.conn.execute("INSERT INTO scoped_tags (name) VALUES (?)", (kwargs["name"],))
        return int(cursor.lastrowid) + 100

    def link_scoped_memory_tag(self, scope, *, memory_id, tag_id, position):
        self.conn.execute(
            "INSERT INTO scoped_memory_tags (memory_id, tag_id) VALUES (?, ?)",
            (memory_id, tag_id),
        )


def _scope():
    return RuntimeScope(
        bot_id="bot-alpha",
        visibility="group",
        session=SessionRef("qq:group:group-1", "qq", "group", "group-1"),
    )


def test_tag_worker_rolls_back_failed_batch_transaction():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
    connection.execute("CREATE TABLE scoped_tags (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute(
        "CREATE TABLE scoped_memory_tags (memory_id INTEGER REFERENCES memories(id), tag_id INTEGER REFERENCES scoped_tags(id))"
    )
    connection.execute("CREATE TABLE tag_extraction_status (memory_id INTEGER PRIMARY KEY, status TEXT, updated_at REAL)")
    connection.execute("INSERT INTO memories (id, content) VALUES (1, '需要提取标签的记忆')")
    connection.commit()

    worker = TagWorker(
        db=_DB(connection),
        tag_extractor=_Extractor(),
        embedding_service=_Embedding(),
        tag_index=SimpleNamespace(),
        config={"max_batch_per_cycle": 1},
    )
    asyncio.run(worker._process_batch([
        TagWorkItem(1, "需要提取标签的记忆", "tester", _scope()),
    ]))

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()[0] == 0
    connection.close()
