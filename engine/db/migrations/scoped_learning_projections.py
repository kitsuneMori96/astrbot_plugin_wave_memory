"""Scoped FewShot 与 reviewed BookLore projection 的幂等增量迁移。

该迁移只在 WaveMemory 主库创建正式 projection 表；不会创建、复制或修改
ExternalBookLore 的 raw Catalog 表。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager


_CREATE_SCOPED_FEWSHOT = """
CREATE TABLE IF NOT EXISTS scoped_few_shot_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_scope_key TEXT NOT NULL,
    runtime_scope_json TEXT NOT NULL,
    content TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    traits_json TEXT NOT NULL DEFAULT '[]',
    candidate_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    evidence_bindings_json TEXT NOT NULL,
    source_tags_json TEXT NOT NULL DEFAULT '[]',
    query_trace_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('approved', 'revoked')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    usage_count INTEGER NOT NULL DEFAULT 0 CHECK(usage_count >= 0),
    positive_feedback_count INTEGER NOT NULL DEFAULT 0 CHECK(positive_feedback_count >= 0),
    negative_feedback_count INTEGER NOT NULL DEFAULT 0 CHECK(negative_feedback_count >= 0),
    last_used_at REAL,
    retired_at REAL,
    retirement_reason TEXT,
    retirement_idempotency_key TEXT,
    source_candidate_id INTEGER,
    idempotency_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    approved_at REAL NOT NULL
)
"""

_CREATE_SCOPED_FEWSHOT_USAGE = """
CREATE TABLE IF NOT EXISTS scoped_few_shot_usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL,
    runtime_scope_key TEXT NOT NULL,
    query_trace_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    used_at REAL NOT NULL,
    FOREIGN KEY(example_id) REFERENCES scoped_few_shot_examples(id)
)
"""

_CREATE_SCOPED_FEWSHOT_FEEDBACK = """
CREATE TABLE IF NOT EXISTS scoped_few_shot_feedback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL,
    runtime_scope_key TEXT NOT NULL,
    query_trace_id TEXT NOT NULL,
    feedback TEXT NOT NULL CHECK(feedback IN ('useful', 'not_useful', 'misleading')),
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    FOREIGN KEY(example_id) REFERENCES scoped_few_shot_examples(id)
)
"""

_CREATE_REVIEWED_BOOK_LORE = """
CREATE TABLE IF NOT EXISTS reviewed_book_lore_projections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_catalog_scope_key TEXT NOT NULL,
    source_catalog_scope_json TEXT NOT NULL,
    target_runtime_scope_key TEXT NOT NULL,
    target_runtime_scope_json TEXT NOT NULL,
    community_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL,
    rank REAL NOT NULL DEFAULT 0,
    candidate_json TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    evidence_bindings_json TEXT NOT NULL,
    evidence_derivation_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'rejected', 'revoked')),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    source_candidate_id INTEGER,
    idempotency_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    approved_at REAL
)
"""

_CREATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_scoped_few_shot_identity "
    "ON scoped_few_shot_examples(runtime_scope_key, idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_scoped_few_shot_read "
    "ON scoped_few_shot_examples(runtime_scope_key, status, score DESC, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scoped_few_shot_usage_trace "
    "ON scoped_few_shot_usage_events(runtime_scope_key, query_trace_id, example_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_scoped_few_shot_feedback_trace_example "
    "ON scoped_few_shot_feedback_events(runtime_scope_key, query_trace_id, example_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_reviewed_book_lore_identity "
    "ON reviewed_book_lore_projections(target_runtime_scope_key, idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_reviewed_book_lore_read "
    "ON reviewed_book_lore_projections(target_runtime_scope_key, status, rank DESC, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reviewed_book_lore_source "
    "ON reviewed_book_lore_projections(source_catalog_scope_key, community_id)",
)


@contextmanager
def _transaction(connection):
    factory = getattr(connection, "write_transaction", None)
    if callable(factory):
        with factory() as tx:
            yield tx
        return
    if bool(getattr(connection, "in_transaction", False)):
        raise RuntimeError("connection already has an active transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _columns(connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _upgrade_scoped_fewshot(connection) -> None:
    additions = (
        ("source_tags_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("query_trace_id", "TEXT NOT NULL DEFAULT ''"),
        ("usage_count", "INTEGER NOT NULL DEFAULT 0"),
        ("positive_feedback_count", "INTEGER NOT NULL DEFAULT 0"),
        ("negative_feedback_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_used_at", "REAL"),
        ("retired_at", "REAL"),
        ("retirement_reason", "TEXT"),
        ("retirement_idempotency_key", "TEXT"),
    )
    existing = _columns(connection, "scoped_few_shot_examples")
    for name, definition in additions:
        if name not in existing:
            connection.execute(
                f"ALTER TABLE scoped_few_shot_examples ADD COLUMN {name} {definition}"
            )


def ensure_scoped_learning_projection_schema(connection) -> None:
    """创建 scoped projection 表和索引；可加入调用方已经开启的正式事务。"""
    def apply(tx) -> None:
        tx.execute(_CREATE_SCOPED_FEWSHOT)
        _upgrade_scoped_fewshot(tx)
        tx.execute(_CREATE_SCOPED_FEWSHOT_USAGE)
        tx.execute(_CREATE_SCOPED_FEWSHOT_FEEDBACK)
        tx.execute(_CREATE_REVIEWED_BOOK_LORE)
        for statement in _CREATE_INDEXES:
            tx.execute(statement)

    if bool(getattr(connection, "in_transaction", False)):
        apply(connection)
        return
    with _transaction(connection) as tx:
        apply(tx)


def run_migration(db_path: str) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        ensure_scoped_learning_projection_schema(connection)
        return True
    except Exception:
        connection.rollback()
        return False
    finally:
        connection.close()


__all__ = ["ensure_scoped_learning_projection_schema", "run_migration"]
