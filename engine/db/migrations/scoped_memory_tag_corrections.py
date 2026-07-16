"""纯增量、可重复的 scoped Memory Tag 人工纠正 schema。"""

from __future__ import annotations

import sqlite3

from ..connection import ConnectionManager


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_memory_tag_corrections (
    correction_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    memory_id INTEGER NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('add', 'remove', 'replace')),
    requested_tags_json TEXT NOT NULL,
    before_tags_json TEXT NOT NULL,
    after_tags_json TEXT NOT NULL,
    memory_revision_before INTEGER NOT NULL,
    memory_revision_after INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'undone')),
    correction_revision INTEGER NOT NULL DEFAULT 1,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    undone_at REAL,
    undone_by_operation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_scoped_memory_tag_corrections_effective
    ON scoped_memory_tag_corrections (
        bot_id, session_id, visibility, memory_id, status, created_at DESC
    );
"""


def _apply(connection: sqlite3.Connection) -> None:
    statements = tuple(statement.strip() for statement in _SCHEMA.split(";") if statement.strip())
    for statement in statements:
        connection.execute(statement)
    columns = {
        str(row[1])
        for row in connection.execute(
            'PRAGMA table_info("scoped_memory_tag_corrections")'
        ).fetchall()
    }
    if "reason" not in columns:
        connection.execute(
            "ALTER TABLE scoped_memory_tag_corrections ADD COLUMN reason TEXT NOT NULL DEFAULT ''"
        )


def ensure_scoped_memory_tag_correction_schema(cm: ConnectionManager) -> None:
    """只新增 scoped correction 表/索引，不读取或改写任何 Tag 基线。"""
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as transaction:
        _apply(transaction)


def ensure_scoped_memory_tag_correction_schema_connection(
    connection: sqlite3.Connection,
) -> None:
    """供 writer-owned bootstrap connection 执行同一增量 schema。"""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    _apply(connection)


__all__ = [
    "ensure_scoped_memory_tag_correction_schema",
    "ensure_scoped_memory_tag_correction_schema_connection",
]
