"""纯增量的 scoped effective Tag projection schema。"""

from __future__ import annotations

import sqlite3

from ..connection import ConnectionManager


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_memory_effective_tags (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    memory_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    relevance REAL NOT NULL DEFAULT 1.0,
    source TEXT NOT NULL CHECK (source IN ('automatic', 'manual')),
    correction_id TEXT,
    projection_revision INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    PRIMARY KEY (bot_id, session_id, visibility, memory_id, tag_id)
);

CREATE TABLE IF NOT EXISTS scoped_tag_projection_state (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'ready', 'failed')),
    projection_revision INTEGER NOT NULL DEFAULT 0,
    cursor_memory_id INTEGER,
    last_error TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (bot_id, session_id, visibility)
);

CREATE INDEX IF NOT EXISTS idx_scoped_memory_effective_tags_scope_memory
    ON scoped_memory_effective_tags (bot_id, session_id, visibility, memory_id, position);
CREATE INDEX IF NOT EXISTS idx_scoped_memory_effective_tags_scope_tag
    ON scoped_memory_effective_tags (bot_id, session_id, visibility, tag_id, memory_id);
"""


def _apply(connection: sqlite3.Connection) -> None:
    for statement in (part.strip() for part in _SCHEMA.split(";") if part.strip()):
        connection.execute(statement)


def ensure_scoped_tag_projection_schema(cm: ConnectionManager) -> None:
    """创建 effective projection 表，不读取或改写任何 legacy 表。"""
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as transaction:
        _apply(transaction)


def ensure_scoped_tag_projection_schema_connection(connection: sqlite3.Connection) -> None:
    """供 writer-owned connection 和 focused tests 使用的同一增量 schema。"""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    _apply(connection)


__all__ = [
    "ensure_scoped_tag_projection_schema",
    "ensure_scoped_tag_projection_schema_connection",
]
