"""tag_extraction_status 完整性迁移测试。"""

from __future__ import annotations

import sqlite3

from engine.db.connection import ConnectionManager
from engine.db.migrations.tag_extraction_status_integrity import (
    ensure_tag_extraction_status_integrity,
)


REQUIRED_COLUMNS = {
    "memory_id",
    "status",
    "attempts",
    "last_error",
    "last_run_at",
    "updated_at",
}


def _columns(manager: ConnectionManager) -> set[str]:
    return {
        str(row[1])
        for row in manager._write_conn.execute("PRAGMA table_info(tag_extraction_status)").fetchall()
    }


def _has_memory_cascade_fk(manager: ConnectionManager) -> bool:
    return any(
        str(row[2]) == "memories"
        and str(row[3]) == "memory_id"
        and str(row[4]) == "id"
        and str(row[6]).upper() == "CASCADE"
        for row in manager._write_conn.execute("PRAGMA foreign_key_list(tag_extraction_status)").fetchall()
    )


def test_status_migration_rebuilds_missing_columns_cleans_orphans_and_cascades(tmp_path):
    path = tmp_path / "legacy-status.sqlite3"
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            """
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at REAL
            );
            """
        )
        raw.execute("INSERT INTO memories(id, content) VALUES (1, 'parent')")
        raw.executemany(
            "INSERT INTO tag_extraction_status(memory_id, status, updated_at) VALUES (?, ?, ?)",
            [(1, "done", 100.0), (99, "failed", 200.0)],
        )
        raw.commit()
    finally:
        raw.close()

    manager = ConnectionManager(str(path))
    try:
        ensure_tag_extraction_status_integrity(manager)
        ensure_tag_extraction_status_integrity(manager)

        assert REQUIRED_COLUMNS <= _columns(manager)
        assert _has_memory_cascade_fk(manager)
        assert manager.execute_read(
            """SELECT memory_id, status, attempts, last_error, last_run_at, updated_at
                 FROM tag_extraction_status"""
        ).fetchall() == [(1, "done", 0, None, None, 100.0)]
        assert manager.execute_read("PRAGMA foreign_key_check(tag_extraction_status)").fetchall() == []

        manager.execute_write("DELETE FROM memories WHERE id=1")
        manager.commit()
        assert manager.execute_read("SELECT * FROM tag_extraction_status").fetchall() == []
    finally:
        manager.close()


def test_status_migration_cleans_orphans_from_otherwise_current_schema(tmp_path):
    path = tmp_path / "current-schema-orphan.sqlite3"
    raw = sqlite3.connect(path)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.executescript(
            """
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                last_run_at REAL,
                updated_at REAL,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );
            """
        )
        raw.execute("INSERT INTO memories(id, content) VALUES (3, 'parent')")
        raw.executemany(
            "INSERT INTO tag_extraction_status(memory_id, status) VALUES (?, ?)",
            [(3, "done"), (4, "done")],
        )
        raw.commit()
    finally:
        raw.close()

    manager = ConnectionManager(str(path))
    try:
        ensure_tag_extraction_status_integrity(manager)

        assert manager._write_conn.execute(
            "SELECT memory_id FROM tag_extraction_status ORDER BY memory_id"
        ).fetchall() == [(3,)]
        assert manager._write_conn.execute(
            "PRAGMA foreign_key_check(tag_extraction_status)"
        ).fetchall() == []
    finally:
        manager.close()


def test_status_migration_rebuilds_full_legacy_schema_without_foreign_key(tmp_path):
    path = tmp_path / "missing-fk.sqlite3"
    raw = sqlite3.connect(path)
    try:
        raw.executescript(
            """
            CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                last_run_at REAL,
                updated_at REAL
            );
            """
        )
        raw.execute("INSERT INTO memories(id, content) VALUES (7, 'parent')")
        raw.executemany(
            """INSERT INTO tag_extraction_status(
                   memory_id, status, attempts, last_error, last_run_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            [(7, "failed", 3, "timeout", 10.0, 11.0), (8, "done", 0, None, 12.0, 13.0)],
        )
        raw.commit()
    finally:
        raw.close()

    manager = ConnectionManager(str(path))
    try:
        ensure_tag_extraction_status_integrity(manager)

        assert _has_memory_cascade_fk(manager)
        assert manager.execute_read(
            """SELECT memory_id, status, attempts, last_error, last_run_at, updated_at
                 FROM tag_extraction_status"""
        ).fetchall() == [(7, "failed", 3, "timeout", 10.0, 11.0)]
        assert manager.execute_read("PRAGMA foreign_key_check(tag_extraction_status)").fetchall() == []
    finally:
        manager.close()
