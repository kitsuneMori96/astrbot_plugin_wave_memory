from __future__ import annotations

import json
import sqlite3

from domain.scope import RuntimeScope, SessionRef
from engine.db.migrations.scoped_memory_tag_corrections import (
    ensure_scoped_memory_tag_correction_schema_connection,
)
from engine.db.migrations.scoped_tag_projection import (
    ensure_scoped_tag_projection_schema_connection,
)
from engine.db.scoped_tag_projection import (
    effective_tag_rows,
    rebuild_memory_effective_tags,
)


def scope() -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-alpha",
        visibility="group",
        session=SessionRef("qq:group:g1", "qq", "group", "g1"),
    )


def connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE scoped_tags(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visibility TEXT NOT NULL,
            name TEXT NOT NULL,
            tag_type TEXT NOT NULL DEFAULT 'keyword',
            description TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(bot_id, session_id, visibility, name)
        );
        CREATE TABLE scoped_memory_tags(
            bot_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            visibility TEXT NOT NULL,
            memory_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            relevance REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY(bot_id, session_id, visibility, memory_id, tag_id)
        );
        """
    )
    ensure_scoped_memory_tag_correction_schema_connection(conn)
    ensure_scoped_tag_projection_schema_connection(conn)
    return conn


def add_tag(conn: sqlite3.Connection, name: str) -> int:
    cursor = conn.execute(
        """INSERT INTO scoped_tags(
               bot_id, session_id, visibility, name, tag_type, confidence,
               metadata, created_at, updated_at)
           VALUES ('bot-alpha', 'qq:group:g1', 'group', ?, 'topic', .9, '{}', 1, 1)""",
        (name,),
    )
    return int(cursor.lastrowid)


def test_effective_projection_keeps_automatic_baseline_and_applies_correction():
    conn = connection()
    automatic_id = add_tag(conn, "自动标签")
    manual_id = add_tag(conn, "人工标签")
    conn.execute(
        """INSERT INTO scoped_memory_tags(
               bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at)
           VALUES ('bot-alpha', 'qq:group:g1', 'group', 1, ?, 1, .8, 1)""",
        (automatic_id,),
    )
    conn.execute(
        """INSERT INTO scoped_memory_tag_corrections(
               correction_id, operation_id, bot_id, session_id, visibility, memory_id,
               operation, requested_tags_json, before_tags_json, after_tags_json,
               memory_revision_before, memory_revision_after, status, correction_revision,
               actor, reason, created_at)
           VALUES ('correction-1', 'operation-1', 'bot-alpha', 'qq:group:g1', 'group', 1,
                   'add', '[\"人工标签\"]', '[\"自动标签\"]', '[\"自动标签\",\"人工标签\"]',
                   1, 2, 'active', 1, 'test', '补充', 2)"""
    )
    conn.commit()

    rows = effective_tag_rows(conn, scope=scope(), memory_id=1)
    assert [(row["tag_id"], row["source"]) for row in rows] == [
        (automatic_id, "automatic"),
        (manual_id, "manual"),
    ]

    revision = rebuild_memory_effective_tags(conn, scope=scope(), memory_id=1, now=3)
    assert revision == 1
    assert conn.execute(
        "SELECT tag_id, source, correction_id FROM scoped_memory_effective_tags ORDER BY position"
    ).fetchall() == [
        (automatic_id, "automatic", "correction-1"),
        (manual_id, "manual", "correction-1"),
    ]


def test_undone_correction_falls_back_to_automatic_rows():
    conn = connection()
    automatic_id = add_tag(conn, "自动标签")
    manual_id = add_tag(conn, "人工标签")
    conn.execute(
        """INSERT INTO scoped_memory_tags(
               bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at)
           VALUES ('bot-alpha', 'qq:group:g1', 'group', 1, ?, 1, 1, 1)""",
        (automatic_id,),
    )
    conn.execute(
        """INSERT INTO scoped_memory_tag_corrections(
               correction_id, operation_id, bot_id, session_id, visibility, memory_id,
               operation, requested_tags_json, before_tags_json, after_tags_json,
               memory_revision_before, memory_revision_after, status, correction_revision,
               actor, reason, created_at, undone_at)
           VALUES ('correction-1', 'operation-1', 'bot-alpha', 'qq:group:g1', 'group', 1,
                   'add', '[\"人工标签\"]', '[\"自动标签\"]', '[\"自动标签\",\"人工标签\"]',
                   1, 2, 'undone', 2, 'test', '补充', 2, 3)"""
    )
    conn.commit()

    rows = effective_tag_rows(conn, scope=scope(), memory_id=1)
    assert [(row["tag_id"], row["source"]) for row in rows] == [(automatic_id, "automatic")]
    assert json.loads(conn.execute("SELECT after_tags_json FROM scoped_memory_tag_corrections").fetchone()[0]) == ["自动标签", "人工标签"]


def test_tag_id_filter_is_pushed_into_sql_and_bounds_the_scan():
    """Cold recall must not scan every scoped link to answer a few tag ids."""
    conn = connection()
    wanted_id = add_tag(conn, "命中标签")
    other_id = add_tag(conn, "无关标签")
    for memory_id in range(1, 51):
        tag_id = wanted_id if memory_id == 1 else other_id
        conn.execute(
            """INSERT INTO scoped_memory_tags(
                   bot_id, session_id, visibility, memory_id, tag_id, position, relevance, created_at)
               VALUES ('bot-alpha', 'qq:group:g1', 'group', ?, ?, 1, 1, 1)""",
            (memory_id, tag_id),
        )
    conn.commit()

    class TracingConnection:
        """Record SQL so the test proves the filter reached SQLite, not Python."""

        def __init__(self, delegate: sqlite3.Connection) -> None:
            self._delegate = delegate
            self.statements: list[str] = []

        def execute(self, sql, *args, **kwargs):
            self.statements.append(sql)
            return self._delegate.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    tracing = TracingConnection(conn)
    rows = effective_tag_rows(tracing, scope=scope(), tag_ids=[wanted_id])

    assert [(row["memory_id"], row["tag_id"]) for row in rows] == [(1, wanted_id)]
    baseline_sql = next(sql for sql in tracing.statements if "FROM scoped_memory_tags" in sql)
    assert "mt.tag_id IN (" in baseline_sql

    # An explicitly empty filter means "no tag matched", not "return everything".
    assert effective_tag_rows(conn, scope=scope(), tag_ids=[]) == []
    # No filter keeps the previous full-baseline behaviour for callers that need it.
    assert len(effective_tag_rows(conn, scope=scope())) == 50
