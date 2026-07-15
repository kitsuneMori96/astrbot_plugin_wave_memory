"""纯增量、可重复的 scoped derived knowledge schema 迁移。

本迁移只创建或 ALTER ``scoped_*`` 表和索引：不会 ALTER、UPDATE、回填或以任何
方式读取后改写 legacy 的 jargon/facts/tags/memory_tags/tag_relations/beliefs/kv_store。
旧数据没有可验证的完整 RuntimeScope，因此只能由显式审核迁移流程处理。
"""

from __future__ import annotations

from ..connection import ConnectionManager


_SCOPED_DERIVED_KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_jargon (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    word TEXT NOT NULL,
    meaning TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    is_jargon INTEGER,
    frequency INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.0,
    contexts TEXT NOT NULL DEFAULT '[]',
    source_memory_id INTEGER,
    source_context TEXT,
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (bot_id, session_id, visibility, word)
);

CREATE TABLE IF NOT EXISTS scoped_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    source_memory_id INTEGER,
    provenance TEXT NOT NULL DEFAULT '{}',
    valid_from REAL,
    valid_until REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    UNIQUE (bot_id, session_id, visibility, subject, predicate, object)
);

CREATE TABLE IF NOT EXISTS scoped_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    name TEXT NOT NULL,
    tag_type TEXT NOT NULL DEFAULT 'keyword',
    description TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (bot_id, session_id, visibility, name)
);

CREATE TABLE IF NOT EXISTS scoped_memory_tags (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    memory_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    relevance REAL NOT NULL DEFAULT 1.0,
    created_at REAL NOT NULL,
    PRIMARY KEY (bot_id, session_id, visibility, memory_id, tag_id),
    FOREIGN KEY (tag_id) REFERENCES scoped_tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scoped_tag_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    source_tag_id INTEGER NOT NULL,
    target_tag_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    confidence REAL NOT NULL DEFAULT 0.0,
    metadata TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'active',
    valid_until REAL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (bot_id, session_id, visibility, source_tag_id, target_tag_id, relation_type),
    FOREIGN KEY (source_tag_id) REFERENCES scoped_tags(id) ON DELETE CASCADE,
    FOREIGN KEY (target_tag_id) REFERENCES scoped_tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scoped_beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    belief_key TEXT NOT NULL,
    content TEXT NOT NULL,
    belief_type TEXT NOT NULL DEFAULT 'world_view',
    strength REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending',
    source_memory_id INTEGER,
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (bot_id, session_id, visibility, belief_key)
);

CREATE TABLE IF NOT EXISTS scoped_consolidation_cursors (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    cursor_name TEXT NOT NULL,
    cursor_value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (bot_id, session_id, visibility, cursor_name)
);

CREATE INDEX IF NOT EXISTS idx_scoped_jargon_scope_status
    ON scoped_jargon (bot_id, session_id, visibility, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_scoped_facts_scope_subject
    ON scoped_facts (bot_id, session_id, visibility, subject, updated_at);
CREATE INDEX IF NOT EXISTS idx_scoped_tags_scope_name
    ON scoped_tags (bot_id, session_id, visibility, name);
CREATE INDEX IF NOT EXISTS idx_scoped_memory_tags_scope_memory
    ON scoped_memory_tags (bot_id, session_id, visibility, memory_id);
CREATE INDEX IF NOT EXISTS idx_scoped_tag_relations_scope_source
    ON scoped_tag_relations (bot_id, session_id, visibility, source_tag_id);
CREATE INDEX IF NOT EXISTS idx_scoped_beliefs_scope_status
    ON scoped_beliefs (bot_id, session_id, visibility, status, updated_at);
"""


def _columns(tx, table: str) -> set[str]:
    return {str(row[1]) for row in tx.execute(f'PRAGMA table_info("{table}")').fetchall()}


def ensure_scoped_derived_knowledge_schema(cm: ConnectionManager) -> None:
    """建立或幂等升级 scoped 派生知识表，且绝不触碰 legacy 表。"""
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    # sqlite3.Connection.executescript() 会隐式提交未完成事务；逐句执行才能
    # 保持 ConnectionManager 提供的单一原子 migration_transaction 语义。
    statements = tuple(
        statement.strip()
        for statement in _SCOPED_DERIVED_KNOWLEDGE_SCHEMA.split(";")
        if statement.strip()
    )
    with cm.migration_transaction() as tx:
        for statement in statements:
            tx.execute(statement)

        # 旧版 scoped schema 允许原地纯增量升级；这里只 ALTER scoped_* 表，
        # 不读取、回填或修改任何 legacy facts/tag_relations 表。
        fact_columns = _columns(tx, "scoped_facts")
        if "revision" not in fact_columns:
            tx.execute("ALTER TABLE scoped_facts ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")

        relation_columns = _columns(tx, "scoped_tag_relations")
        if "status" not in relation_columns:
            tx.execute(
                "ALTER TABLE scoped_tag_relations ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
            )
        if "valid_until" not in relation_columns:
            tx.execute("ALTER TABLE scoped_tag_relations ADD COLUMN valid_until REAL")
        if "revision" not in relation_columns:
            tx.execute(
                "ALTER TABLE scoped_tag_relations ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )


__all__ = ["ensure_scoped_derived_knowledge_schema"]
