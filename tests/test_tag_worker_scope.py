"""TagWorker 的 scoped 正式路径回归测试。"""

from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, debug=lambda *args, **kwargs: None)
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from domain.scope import RuntimeScope, SessionRef
from engine.database import WaveMemoryDB
from services.tag_worker import TagWorker


class _Extractor:
    async def extract_tags_batch(self, messages):
        return [[{"name": "作用域标签", "type": "topic", "confidence": 0.9}] for _ in messages]


class _NoLegacyEmbedding:
    async def get_embedding(self, name):  # pragma: no cover - 调用即代表正式路径退回 legacy 向量逻辑
        raise AssertionError("TagWorker scoped path must not calculate a legacy tag embedding")


class _NoLegacyIndex:
    def add(self, *args, **kwargs):  # pragma: no cover - 调用即代表正式路径写入 legacy 索引
        raise AssertionError("TagWorker scoped path must not write tag_index")


def _scope(bot_id: str, group_id: str = "group-1") -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(f"qq:group:{group_id}", "qq", "group", group_id),
    )


def _insert_memory(db, *, group_id, bot_id, session_id, visibility, resolution_state, quarantine, content):
    cursor = db.conn.execute(
        """INSERT INTO memories (
                group_id, content, timestamp, source, bot_id, session_id, visibility,
                resolution_state, quarantine
            ) VALUES (?, ?, ?, 'live', ?, ?, ?, ?, ?)""",
        (group_id, content, 100.0, bot_id, session_id, visibility, resolution_state, quarantine),
    )
    return int(cursor.lastrowid)


def test_tag_worker_writes_only_scoped_tags_for_canonical_resolved_group_memories(tmp_path):
    db = WaveMemoryDB(str(tmp_path / "wave-memory.sqlite3"), dimension=4)
    try:
        alpha = _scope("bot-alpha")
        beta = _scope("bot-beta")
        alpha_memory = db.add_memory("group-1", "alpha 可安全提取标签的消息", scope=alpha)
        beta_memory = db.add_memory("group-1", "beta 可安全提取标签的消息", scope=beta)
        pretagged_memory = db.add_memory("group-1", "已有一个正式标签的恢复记忆不应再次调用 LLM", scope=alpha)
        tag_id = db.conn.execute(
            """INSERT INTO scoped_tags(
                   bot_id, session_id, visibility, name, tag_type, description,
                   confidence, metadata, created_at, updated_at
               ) VALUES (?, ?, ?, '既有标签', 'topic', '', 0.9, '{}', 100.0, 100.0)""",
            (alpha.bot_id, alpha.session.id, alpha.visibility),
        ).lastrowid
        db.conn.execute(
            """INSERT INTO scoped_memory_tags(
                   bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at
               ) VALUES (?, ?, ?, ?, ?, 1, 0.9, 100.0)""",
            (alpha.bot_id, alpha.session.id, alpha.visibility, pretagged_memory, tag_id),
        )
        malformed_scope_memory = _insert_memory(
            db,
            group_id="group-1",
            bot_id="bot-alpha",
            session_id="qq:group:other-group",
            visibility="group",
            resolution_state="resolved",
            quarantine=0,
            content="session 不对应 group 的记忆必须跳过",
        )
        unresolved_memory = _insert_memory(
            db,
            group_id="group-1",
            bot_id="bot-alpha",
            session_id="qq:group:group-1",
            visibility="group",
            resolution_state="unresolved_legacy",
            quarantine=0,
            content="unresolved 记忆不得产生正式派生标签",
        )
        quarantined_memory = _insert_memory(
            db,
            group_id="group-1",
            bot_id="bot-alpha",
            session_id="qq:group:group-1",
            visibility="group",
            resolution_state="resolved",
            quarantine=1,
            content="quarantine 记忆不得产生正式派生标签",
        )
        db.conn.commit()

        worker = TagWorker(
            db=db,
            tag_extractor=_Extractor(),
            embedding_service=_NoLegacyEmbedding(),
            tag_index=_NoLegacyIndex(),
            config={"max_batch_per_cycle": 10},
        )
        batch = worker._fetch_untagged_batch()

        assert {item.memory_id for item in batch} == {alpha_memory, beta_memory}
        assert {item.scope for item in batch} == {alpha, beta}
        assert db.conn.execute(
            "SELECT status FROM tag_extraction_status WHERE memory_id=?", (malformed_scope_memory,)
        ).fetchone()[0] == "skipped"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM tag_extraction_status WHERE memory_id IN (?, ?)",
            (unresolved_memory, quarantined_memory),
        ).fetchone()[0] == 0

        asyncio.run(worker._process_batch(batch))

        assert db.conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM memory_tags").fetchone()[0] == 0
        assert db.conn.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()[0] == 3
        assert db.conn.execute("SELECT COUNT(*) FROM scoped_memory_tags").fetchone()[0] == 3
        assert db.conn.execute(
            "SELECT COUNT(*) FROM scoped_memory_tags WHERE memory_id IN (?, ?)",
            (unresolved_memory, quarantined_memory),
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM scoped_memory_tags WHERE memory_id=?",
            (pretagged_memory,),
        ).fetchone()[0] == 1
        assert db.conn.execute(
            "SELECT COUNT(*) FROM tag_extraction_status WHERE memory_id IN (?, ?) AND status='done'",
            (alpha_memory, beta_memory),
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_tag_worker_reuses_existing_legacy_links_and_backfills_only_untagged_legacy_rows(tmp_path):
    db = WaveMemoryDB(str(tmp_path / "legacy-wave-memory.sqlite3"), dimension=4)
    try:
        untagged_id = db.conn.execute(
            "INSERT INTO memories(group_id, content, timestamp, source) VALUES (?, ?, ?, ?)",
            ("group-1", "未打标签的旧群聊记忆需要补提取", 100.0, "legacy"),
        ).lastrowid
        pretagged_id = db.conn.execute(
            "INSERT INTO memories(group_id, content, timestamp, source) VALUES (?, ?, ?, ?)",
            ("group-1", "已有旧标签的记忆不应重复提取", 101.0, "legacy"),
        ).lastrowid
        old_tag_id = db.add_tag("历史已有标签")
        db.conn.execute(
            "INSERT INTO memory_tags(memory_id, tag_id, position, relevance) VALUES (?, ?, 1, 1.0)",
            (pretagged_id, old_tag_id),
        )
        db.conn.commit()

        worker = TagWorker(
            db=db,
            tag_extractor=_Extractor(),
            embedding_service=None,
            tag_index=_NoLegacyIndex(),
            config={"max_batch_per_cycle": 10},
        )
        batch = worker._fetch_untagged_batch()

        assert [item.memory_id for item in batch] == [untagged_id]
        assert batch[0].scope is None
        assert batch[0].legacy_group_id == "group-1"

        asyncio.run(worker._process_batch(batch))

        assert db.conn.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE memory_id=?", (untagged_id,)
        ).fetchone()[0] == 1
        assert db.conn.execute(
            "SELECT COUNT(*) FROM scoped_memory_tags WHERE memory_id=?", (untagged_id,)
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT status FROM tag_extraction_status WHERE memory_id=?", (untagged_id,)
        ).fetchone()[0] == "done"
        assert db.conn.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE memory_id=?", (pretagged_id,)
        ).fetchone()[0] == 1
    finally:
        db.close()
