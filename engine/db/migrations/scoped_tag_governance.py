"""纯增量的 scoped Tag 治理 schema。

只扩展 scoped_tags 并创建 scoped_tag_audit_suggestions；legacy tags、tag_audit_suggestions
和 memory_tags 永远不会由此迁移读取、回填或改写。
"""

from __future__ import annotations

import sqlite3

from ..connection import ConnectionManager


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scoped_tag_audit_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    action TEXT NOT NULL CHECK (action IN ('merge', 'retype', 'alias', 'deactivate')),
    tag_ids_json TEXT NOT NULL,
    target_tag_id INTEGER,
    target_name TEXT,
    target_type TEXT,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'conflict', 'expired')),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    expires_at REAL,
    resolved_at REAL,
    resolved_by TEXT,
    resolution_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_scoped_tag_audit_suggestions_scope_status
    ON scoped_tag_audit_suggestions (bot_id, session_id, visibility, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_scoped_tag_audit_suggestions_operation
    ON scoped_tag_audit_suggestions (operation_id);

CREATE TABLE IF NOT EXISTS scoped_tag_governance_changes (
    operation_id TEXT NOT NULL,
    suggestion_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (operation_id, suggestion_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_scoped_tag_governance_changes_suggestion
    ON scoped_tag_governance_changes (suggestion_id, operation_id);

CREATE TABLE IF NOT EXISTS scoped_tag_governance_compensations (
    compensation_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    suggestion_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility = 'group'),
    expected_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('committed', 'conflict')),
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    resolved_at REAL
);
CREATE INDEX IF NOT EXISTS idx_scoped_tag_governance_compensations_suggestion
    ON scoped_tag_governance_compensations (suggestion_id, created_at DESC);
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _apply(connection: sqlite3.Connection) -> None:
    for statement in (part.strip() for part in _SCHEMA.split(';') if part.strip()):
        connection.execute(statement)
    columns = _columns(connection, "scoped_tags")
    additions = (
        ("revision", "INTEGER NOT NULL DEFAULT 1"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("aliases", "TEXT NOT NULL DEFAULT '[]'"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE scoped_tags ADD COLUMN {name} {definition}")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_scoped_tags_scope_status_revision "
        "ON scoped_tags (bot_id, session_id, visibility, status, revision)"
    )


def ensure_scoped_tag_governance_schema(cm: ConnectionManager) -> None:
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as transaction:
        _apply(transaction)


def ensure_scoped_tag_governance_schema_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    _apply(connection)


__all__ = [
    "ensure_scoped_tag_governance_schema",
    "ensure_scoped_tag_governance_schema_connection",
]
