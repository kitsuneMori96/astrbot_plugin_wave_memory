"""Incremental shared-memory grant schema (read authorization, not fanout).

A grant never inserts memories rows into the consumer Scope.  It only records
that a consumer Scope may *read* an owned memory under explicit policy.
"""

from __future__ import annotations

from ..connection import ConnectionManager

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_memory_grants (
    grant_id TEXT PRIMARY KEY,
    owner_bot_id TEXT NOT NULL,
    owner_session_id TEXT NOT NULL,
    owner_visibility TEXT NOT NULL CHECK (owner_visibility = 'group'),
    owner_group_id TEXT NOT NULL,
    memory_id INTEGER NOT NULL,
    consumer_bot_id TEXT NOT NULL,
    consumer_session_id TEXT NOT NULL,
    consumer_visibility TEXT NOT NULL CHECK (consumer_visibility = 'group'),
    consumer_group_id TEXT NOT NULL,
    grant_mode TEXT NOT NULL DEFAULT 'read' CHECK (grant_mode = 'read'),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'system',
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    revoked_at REAL,
    UNIQUE(
        owner_bot_id, owner_session_id, owner_visibility, memory_id,
        consumer_bot_id, consumer_session_id, consumer_visibility, grant_mode
    )
);
CREATE INDEX IF NOT EXISTS idx_shared_memory_grants_consumer_active
    ON shared_memory_grants(
        consumer_bot_id, consumer_session_id, consumer_visibility, status, memory_id
    );
CREATE INDEX IF NOT EXISTS idx_shared_memory_grants_owner_memory
    ON shared_memory_grants(
        owner_bot_id, owner_session_id, owner_visibility, memory_id, status
    );
CREATE INDEX IF NOT EXISTS idx_shared_memory_grants_consumer_group
    ON shared_memory_grants(consumer_group_id, status, memory_id);
"""


def ensure_shared_memory_grants_schema(cm: ConnectionManager) -> None:
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as tx:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                tx.execute(statement)


__all__ = ["ensure_shared_memory_grants_schema"]
