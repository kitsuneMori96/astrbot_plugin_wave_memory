"""Unify tag_pair_similarity column names with the production/read path.

Historical code created ``(tag_a, tag_b, similarity, computed_at)`` while the
maintenance writer and PairSimilarityService use
``(tag_id_a, tag_id_b, similarity, updated_at)``.  Existing production DBs
already use the latter; this migration only rewrites the legacy shape.
"""

from __future__ import annotations

import sqlite3

from ..connection import ConnectionManager


_TABLE = "tag_pair_similarity"
_CANONICAL_COLUMNS = {"tag_id_a", "tag_id_b", "similarity", "updated_at"}
_LEGACY_COLUMNS = {"tag_a", "tag_b", "similarity", "computed_at"}
_SCHEMA = """
CREATE TABLE tag_pair_similarity (
    tag_id_a INTEGER NOT NULL,
    tag_id_b INTEGER NOT NULL,
    similarity REAL NOT NULL,
    updated_at REAL,
    PRIMARY KEY (tag_id_a, tag_id_b)
)
"""


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _apply(connection: sqlite3.Connection) -> None:
    if not _table_exists(connection, _TABLE):
        connection.execute(_SCHEMA)
        return

    columns = _columns(connection, _TABLE)
    if _CANONICAL_COLUMNS <= columns:
        return
    if not {"tag_a", "tag_b", "similarity"} <= columns:
        # Unknown shape: rebuild empty canonical table rather than guess.
        connection.execute(f"DROP TABLE {_TABLE}")
        connection.execute(_SCHEMA)
        return

    temporary = "tag_pair_similarity__schema_tmp"
    connection.execute(f"DROP TABLE IF EXISTS {temporary}")
    connection.execute(_SCHEMA.replace(_TABLE, temporary, 1))
    updated_at = "computed_at" if "computed_at" in columns else "NULL"
    connection.execute(
        f"""INSERT OR IGNORE INTO {temporary} (
                tag_id_a, tag_id_b, similarity, updated_at
            )
            SELECT tag_a, tag_b, similarity, {updated_at}
              FROM {_TABLE}
             WHERE tag_a IS NOT NULL AND tag_b IS NOT NULL"""
    )
    connection.execute(f"DROP TABLE {_TABLE}")
    connection.execute(f"ALTER TABLE {temporary} RENAME TO {_TABLE}")


def ensure_tag_pair_similarity_schema(cm: ConnectionManager) -> None:
    if not isinstance(cm, ConnectionManager):
        raise TypeError("cm must be a ConnectionManager")
    with cm.migration_transaction() as transaction:
        _apply(transaction)


def ensure_tag_pair_similarity_schema_connection(connection: sqlite3.Connection) -> None:
    if not isinstance(connection, sqlite3.Connection):
        raise TypeError("connection must be sqlite3.Connection")
    _apply(connection)


__all__ = [
    "ensure_tag_pair_similarity_schema",
    "ensure_tag_pair_similarity_schema_connection",
]
