"""tag_extraction_status 的完整性迁移。"""

from __future__ import annotations

import sqlite3

from ..connection import ConnectionManager


_TABLE = "tag_extraction_status"
_REQUIRED_COLUMNS = {
    "memory_id",
    "status",
    "attempts",
    "last_error",
    "last_run_at",
    "updated_at",
}
_SCHEMA = """
CREATE TABLE tag_extraction_status (
    memory_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_run_at REAL,
    updated_at REAL,
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
)
"""


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _has_memory_cascade_fk(connection: sqlite3.Connection) -> bool:
    return any(
        str(row[2]) == "memories"
        and str(row[3]) == "memory_id"
        and str(row[4]) == "id"
        and str(row[6]).upper() == "CASCADE"
        for row in connection.execute(f"PRAGMA foreign_key_list({_TABLE})").fetchall()
    )


def _create_table(connection: sqlite3.Connection, table: str = _TABLE) -> None:
    connection.execute(_SCHEMA.replace(_TABLE, table, 1))


def _rebuild(connection: sqlite3.Connection, columns: set[str]) -> None:
    temporary = "tag_extraction_status__integrity_tmp"
    connection.execute(f"DROP TABLE IF EXISTS {temporary}")
    _create_table(connection, temporary)

    if "memory_id" in columns:
        status = "COALESCE(status, 'pending')" if "status" in columns else "'pending'"
        attempts = "COALESCE(attempts, 0)" if "attempts" in columns else "0"
        last_error = "last_error" if "last_error" in columns else "NULL"
        last_run_at = "last_run_at" if "last_run_at" in columns else "NULL"
        updated_at = "updated_at" if "updated_at" in columns else "NULL"
        connection.execute(
            f"""INSERT INTO {temporary} (
                    memory_id, status, attempts, last_error, last_run_at, updated_at
                )
                SELECT tes.memory_id, {status}, {attempts}, {last_error}, {last_run_at}, {updated_at}
                  FROM {_TABLE} tes
                 WHERE EXISTS (SELECT 1 FROM memories m WHERE m.id=tes.memory_id)"""
        )

    connection.execute(f"DROP TABLE {_TABLE}")
    connection.execute(f"ALTER TABLE {temporary} RENAME TO {_TABLE}")


def _verify_foreign_keys(connection: sqlite3.Connection) -> None:
    """在 SQLite 支持时仅验证本表，避免误报其它历史表的孤儿。"""
    try:
        violations = connection.execute(f"PRAGMA foreign_key_check({_TABLE})").fetchall()
    except sqlite3.DatabaseError:
        return
    if violations:
        raise RuntimeError("tag_extraction_status foreign key integrity check failed")


def _apply(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, "memories"):
        raise RuntimeError("memories table must exist before tag extraction status migration")

    if not _table_exists(connection, _TABLE):
        _create_table(connection)
    else:
        columns = _columns(connection, _TABLE)
        if not _REQUIRED_COLUMNS <= columns or not _has_memory_cascade_fk(connection):
            _rebuild(connection, columns)

    connection.execute(
        f"""DELETE FROM {_TABLE}
             WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id={_TABLE}.memory_id)"""
    )
    _verify_foreign_keys(connection)


def ensure_tag_extraction_status_integrity(cm: ConnectionManager) -> None:
    """以迁移事务修复状态表列、级联外键和历史孤儿记录。"""
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as transaction:
        _apply(transaction)


def ensure_tag_extraction_status_integrity_connection(connection: sqlite3.Connection) -> None:
    """供 focused tests 和 writer-owned connection 使用的同一迁移。"""
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    _apply(connection)


__all__ = [
    "ensure_tag_extraction_status_integrity",
    "ensure_tag_extraction_status_integrity_connection",
]
