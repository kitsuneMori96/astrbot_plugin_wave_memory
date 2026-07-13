"""独立书中经历表的幂等增量迁移。

该表刻意不复用 ``experience_episodes``：后者只记录 Bot 与真实用户/群聊的互动经历。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS book_experience_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    user_id TEXT,
    content TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    source_candidate_id INTEGER,
    idempotency_key TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""

_CREATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_book_experience_episode_identity "
    "ON book_experience_episodes(bot_id, idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_book_experience_episode_scope "
    "ON book_experience_episodes(bot_id, group_id, user_id, created_at)",
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


def ensure_book_experience_schema(connection) -> None:
    """创建书中经历表和索引；重复调用不会改变已有数据。"""
    with _transaction(connection) as tx:
        tx.execute(_CREATE_TABLE)
        for statement in _CREATE_INDEXES:
            tx.execute(statement)


def run_migration(db_path: str) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        ensure_book_experience_schema(connection)
        return True
    except Exception:
        connection.rollback()
        return False
    finally:
        connection.close()


__all__ = ["ensure_book_experience_schema", "run_migration"]
