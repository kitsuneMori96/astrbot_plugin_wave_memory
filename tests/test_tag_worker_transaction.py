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
        return int(cursor.lastrowid)

    def link_scoped_memory_tag(self, scope, *, memory_id, tag_id, position):
        if memory_id == 1:
            raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")
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


def test_tag_worker_skips_fk_failure_without_rolling_back_other_batch_items():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY, content TEXT, group_id TEXT,
            bot_id TEXT, session_id TEXT, visibility TEXT
        )"""
    )
    connection.execute("CREATE TABLE scoped_tags (id INTEGER PRIMARY KEY, name TEXT)")
    connection.execute(
        "CREATE TABLE scoped_memory_tags (memory_id INTEGER REFERENCES memories(id), tag_id INTEGER REFERENCES scoped_tags(id))"
    )
    connection.execute(
        """CREATE TABLE tag_extraction_status (
            memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, last_run_at REAL, updated_at REAL
        )"""
    )
    connection.executemany(
        """INSERT INTO memories(id, content, group_id, bot_id, session_id, visibility)
           VALUES (?, ?, 'group-1', 'bot-alpha', 'qq:group:group-1', 'group')""",
        [(1, '会触发外键错误的记忆'), (2, '同批有效记忆仍应写入')],
    )
    connection.commit()

    worker = TagWorker(
        db=_DB(connection),
        tag_extractor=_Extractor(),
        embedding_service=_Embedding(),
        tag_index=SimpleNamespace(),
        config={"max_batch_per_cycle": 1},
    )
    asyncio.run(worker._process_batch([
        TagWorkItem(1, "会触发外键错误的记忆", "tester", _scope()),
        TagWorkItem(2, "同批有效记忆仍应写入", "tester", _scope()),
    ]))

    assert connection.in_transaction is False
    assert connection.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()[0] == 1
    assert connection.execute(
        "SELECT memory_id FROM scoped_memory_tags ORDER BY memory_id"
    ).fetchall() == [(2,)]
    assert connection.execute(
        "SELECT memory_id, status, attempts FROM tag_extraction_status ORDER BY memory_id"
    ).fetchall() == [(1, "failed", 1), (2, "done", 0)]
    connection.close()
