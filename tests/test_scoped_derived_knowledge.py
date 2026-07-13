"""scoped derived knowledge 的纯增量迁移和 fail-closed repository 测试。"""

from __future__ import annotations

import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.memory_repo import MemoryRepo
from engine.db.migrations.memories_v2 import ensure_memories_v2_schema
from engine.db.migrations.scoped_derived_knowledge import ensure_scoped_derived_knowledge_schema
from engine.db.scoped_knowledge_repo import ScopedKnowledgeRepo, ScopedKnowledgeScopeError


SCOPED_TABLES = {
    "scoped_jargon",
    "scoped_facts",
    "scoped_tags",
    "scoped_memory_tags",
    "scoped_tag_relations",
    "scoped_beliefs",
    "scoped_consolidation_cursors",
}
LEGACY_TABLES = {
    "jargon",
    "facts",
    "tags",
    "memory_tags",
    "tag_relations",
    "beliefs",
    "kv_store",
}


@pytest.fixture
def manager(tmp_path):
    cm = ConnectionManager(str(tmp_path / "wave_memory.sqlite3"))
    yield cm
    cm.close()


def _scope(*, bot_id: str = "bot-alpha", group_id: str = "group-1") -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"qq:group:{group_id}",
            platform_id="qq",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id="qq:user:10001",
    )


def _legacy_schema_snapshot(cm: ConnectionManager) -> dict[str, tuple[str, tuple]]:
    """建立最小 legacy 表并捕获其 DDL/行，供迁移不改写断言使用。"""
    cm.executescript(
        """
        CREATE TABLE jargon (id INTEGER PRIMARY KEY, word TEXT, group_id TEXT);
        CREATE TABLE facts (id INTEGER PRIMARY KEY, subject TEXT, group_id TEXT);
        CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER);
        CREATE TABLE tag_relations (id INTEGER PRIMARY KEY, source_tag_id INTEGER, target_tag_id INTEGER);
        CREATE TABLE beliefs (id INTEGER PRIMARY KEY, content TEXT, bot_id TEXT);
        CREATE TABLE kv_store (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO jargon VALUES (1, 'legacy-word', 'legacy-group');
        INSERT INTO facts VALUES (1, 'legacy-subject', 'legacy-group');
        INSERT INTO tags VALUES (1, 'legacy-tag');
        INSERT INTO memory_tags VALUES (1, 1);
        INSERT INTO tag_relations VALUES (1, 1, 1);
        INSERT INTO beliefs VALUES (1, 'legacy-belief', 'legacy-bot');
        INSERT INTO kv_store VALUES ('last_consolidation_ts', '123');
        """
    )
    cm.commit()
    return {
        table: (
            cm.execute_read("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0],
            tuple(cm.execute_read(f"SELECT * FROM {table}").fetchall()),
        )
        for table in LEGACY_TABLES
    }


def test_migration_is_idempotent_additive_and_never_rewrites_legacy_tables(manager):
    before = _legacy_schema_snapshot(manager)

    ensure_scoped_derived_knowledge_schema(manager)
    first_schema = {
        table: manager.execute_read("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        for table in SCOPED_TABLES
    }
    ensure_scoped_derived_knowledge_schema(manager)

    tables = {
        row[0]
        for row in manager.execute_read("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert SCOPED_TABLES <= tables
    assert {
        table: manager.execute_read("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        for table in SCOPED_TABLES
    } == first_schema
    after = {
        table: (
            manager.execute_read("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0],
            tuple(manager.execute_read(f"SELECT * FROM {table}").fetchall()),
        )
        for table in LEGACY_TABLES
    }
    assert after == before


def test_scoped_repository_isolated_by_exact_bot_and_session_scope(manager):
    ensure_scoped_derived_knowledge_schema(manager)
    repo = ScopedKnowledgeRepo(manager)
    alpha = _scope()
    same_group_other_bot = _scope(bot_id="bot-beta")
    same_bot_other_session = _scope(group_id="group-2")

    jargon_id = repo.upsert_scoped_jargon(
        alpha, word="团建", meaning="群内固定活动", status="confirmed", is_jargon=True,
        frequency=3, contexts=["今晚团建"], provenance={"review": "approved"},
    )
    fact_id = repo.upsert_scoped_fact(
        alpha, subject="qq:user:10001", predicate="likes", object="猫", status="reviewed",
    )
    tag_id = repo.upsert_scoped_tag(alpha, name="宠物", metadata={"source": "unit"})
    relation_id = repo.upsert_scoped_tag_relation(
        alpha, source_tag_id=tag_id, target_tag_id=tag_id, relation_type="related",
    )
    belief_id = repo.upsert_scoped_belief(
        alpha, belief_key="careful-with-pets", content="要温柔对待宠物", status="reviewed",
    )
    repo.advance_scoped_consolidation_cursor(alpha, cursor_name="messages", cursor_value="42")

    assert all(value > 0 for value in (jargon_id, fact_id, tag_id, relation_id, belief_id))
    assert [item["word"] for item in repo.list_scoped_jargon(alpha, status="confirmed")] == ["团建"]
    assert [item["object"] for item in repo.list_scoped_facts(alpha, subject="qq:user:10001")] == ["猫"]
    assert [item["belief_key"] for item in repo.list_scoped_beliefs(alpha, status="reviewed")] == ["careful-with-pets"]
    assert repo.get_scoped_consolidation_cursor(alpha, cursor_name="messages") == "42"

    for foreign_scope in (same_group_other_bot, same_bot_other_session):
        assert repo.list_scoped_jargon(foreign_scope) == []
        assert repo.list_scoped_facts(foreign_scope) == []
        assert repo.list_scoped_beliefs(foreign_scope) == []
        assert repo.get_scoped_consolidation_cursor(foreign_scope, cursor_name="messages") is None


def test_formal_api_requires_runtime_scope_and_rejects_cross_scope_links(manager):
    memory_repo = MemoryRepo(manager)
    ensure_memories_v2_schema(manager)
    ensure_scoped_derived_knowledge_schema(manager)
    repo = ScopedKnowledgeRepo(manager)
    alpha = _scope()
    beta = _scope(bot_id="bot-beta")
    private = RuntimeScope(bot_id="bot-alpha", visibility="bot_private", session=None)

    with pytest.raises(ScopedKnowledgeScopeError) as missing:
        repo.upsert_scoped_jargon(None, word="forbidden")
    assert missing.value.reason_code == "scope_required"
    with pytest.raises(ScopedKnowledgeScopeError) as unsupported:
        repo.upsert_scoped_jargon(private, word="forbidden")
    assert unsupported.value.reason_code == "derived_scope_visibility_unsupported"

    alpha_tag = repo.upsert_scoped_tag(alpha, name="alpha-tag")
    beta_tag = repo.upsert_scoped_tag(beta, name="beta-tag")
    with pytest.raises(ScopedKnowledgeScopeError) as cross_tag:
        repo.upsert_scoped_tag_relation(
            alpha, source_tag_id=alpha_tag, target_tag_id=beta_tag, relation_type="related",
        )
    assert cross_tag.value.reason_code == "tag_scope_mismatch"

    manager.execute_write(
        "INSERT INTO memories(group_id, content, timestamp) VALUES (?, ?, ?)",
        ("group-1", "legacy memory", 1.0),
    )
    manager.commit()
    with pytest.raises(ScopedKnowledgeScopeError) as legacy_memory:
        repo.link_scoped_memory_tag(alpha, memory_id=1, tag_id=alpha_tag)
    assert legacy_memory.value.reason_code == "memory_scope_mismatch"

    beta_memory_id = memory_repo.add_memory("group-1", "beta memory", scope=beta)
    with pytest.raises(ScopedKnowledgeScopeError) as wrong_memory_scope:
        repo.link_scoped_memory_tag(alpha, memory_id=beta_memory_id, tag_id=alpha_tag)
    assert wrong_memory_scope.value.reason_code == "memory_scope_mismatch"

    alpha_memory_id = memory_repo.add_memory("group-1", "alpha memory", scope=alpha)
    repo.link_scoped_memory_tag(alpha, memory_id=alpha_memory_id, tag_id=alpha_tag, position=2)
    count = manager.execute_read(
        "SELECT COUNT(*) FROM scoped_memory_tags WHERE memory_id=? AND tag_id=?",
        (alpha_memory_id, alpha_tag),
    ).fetchone()[0]
    assert count == 1
