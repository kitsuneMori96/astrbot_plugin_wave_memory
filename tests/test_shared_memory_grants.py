"""Shared memory grants: schema + repo without physical fanout."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from engine.db.connection import ConnectionManager
from engine.db.migrations.shared_memory_grants import ensure_shared_memory_grants_schema
from engine.db.shared_memory_grant_repo import SharedMemoryGrantRepository


def _scopes():
    owner = {
        "bot_id": "yushu",
        "session_id": "羽书:group:111",
        "visibility": "group",
        "group_id": "111",
    }
    consumer = {
        "bot_id": "yushu",
        "session_id": "羽书:group:222",
        "visibility": "group",
        "group_id": "222",
    }
    return owner, consumer


def test_schema_and_grant_idempotent_no_memory_copy(tmp_path: Path):
    db = tmp_path / "t.db"
    cm = ConnectionManager(str(db))
    ensure_shared_memory_grants_schema(cm)
    # owned memory only in owner group
    with cm.write_transaction() as tx:
        tx.execute(
            """
            CREATE TABLE memories(
                id INTEGER PRIMARY KEY, content TEXT, group_id TEXT
            )
            """
        )
        tx.execute("INSERT INTO memories(id, content, group_id) VALUES (7, 'owned-only', '111')")

    repo = SharedMemoryGrantRepository(cm)
    owner, consumer = _scopes()
    r1 = repo.grant_read(
        owner_scope=owner,
        consumer_scope=consumer,
        memory_id=7,
        reason="semantic-share-pilot",
        actor="test",
    )
    assert r1["created"] is True
    r2 = repo.grant_read(
        owner_scope=owner,
        consumer_scope=consumer,
        memory_id=7,
        reason="again",
        actor="test",
    )
    assert r2["created"] is False
    assert r2["grant_id"] == r1["grant_id"]

    grants = repo.list_active_for_consumer(consumer_scope=consumer)
    assert len(grants) == 1
    assert grants[0]["memory_id"] == 7

    # Critical: no extra memories row for consumer group
    conn = sqlite3.connect(db.as_posix())
    n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_group = conn.execute(
        "SELECT group_id, COUNT(*) FROM memories GROUP BY group_id"
    ).fetchall()
    conn.close()
    assert n == 1
    assert by_group == [("111", 1)]

    rev = repo.revoke(grant_id=r1["grant_id"], actor="test")
    assert rev["revoked"] == 1
    assert repo.list_active_for_consumer(consumer_scope=consumer) == []

    # reactivate
    r3 = repo.grant_read(
        owner_scope=owner,
        consumer_scope=consumer,
        memory_id=7,
        reason="reopen",
        actor="test",
    )
    assert r3["reactivated"] is True
    assert repo.active_memory_ids_for_consumer(consumer_scope=consumer) == [7]
    cm.close()


def test_same_scope_grant_rejected(tmp_path: Path):
    db = tmp_path / "t2.db"
    cm = ConnectionManager(str(db))
    ensure_shared_memory_grants_schema(cm)
    repo = SharedMemoryGrantRepository(cm)
    owner, _ = _scopes()
    try:
        repo.grant_read(owner_scope=owner, consumer_scope=owner, memory_id=1)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "differ" in str(exc)
    cm.close()
