"""memories v2 的纯增量迁移与 Scope 仓储边界测试。"""

from __future__ import annotations

import json

import numpy as np
import pytest

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.memory_repo import MemoryRepo, MemoryScopeError
from engine.db.migrations.memories_v2 import MEMORIES_V2_VERSION, ensure_memories_v2_schema


V2_COLUMNS = {
    "bot_id",
    "session_id",
    "visibility",
    "origin_fingerprint",
    "provenance",
    "version",
    "quarantine",
    "resolution_state",
}


@pytest.fixture
def manager(tmp_path):
    cm = ConnectionManager(str(tmp_path / "wave_memory.sqlite3"))
    yield cm
    cm.close()


def _repo_with_legacy_memories(manager: ConnectionManager) -> MemoryRepo:
    return MemoryRepo(manager)


def _columns(manager: ConnectionManager) -> set[str]:
    return {
        row[1]
        for row in manager.execute_read("PRAGMA table_info(memories)").fetchall()
    }


def _group_scope(*, bot_id: str = "bot-alpha", group_id: str = "group-1") -> RuntimeScope:
    return RuntimeScope(
        bot_id=bot_id,
        visibility="group",
        session=SessionRef(
            id=f"qq:group:{group_id}",
            platform_id="qq",
            kind="group",
            conversation_id=group_id,
        ),
        subject_principal_id="qq:user:10001",
    )


def test_new_legacy_table_receives_all_v2_columns_and_scope_index(manager):
    _repo_with_legacy_memories(manager)

    ensure_memories_v2_schema(manager)

    assert V2_COLUMNS <= _columns(manager)
    indexes = {row[1] for row in manager.execute_read("PRAGMA index_list(memories)").fetchall()}
    assert "idx_memories_v2_scope" in indexes
    assert manager.execute_read("PRAGMA foreign_keys").fetchone()[0] == 1


def test_memory_repo_uses_memories_vector_as_single_source_of_truth(manager):
    repo = _repo_with_legacy_memories(manager)
    ensure_memories_v2_schema(manager)
    vector = np.asarray([0.25, 0.75], dtype=np.float32)

    memory_id = repo.add_memory(
        "group-1",
        "canonical vector",
        vector=vector,
        scope=_group_scope(),
    )

    legacy_table = manager.execute_read(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_vectors'"
    ).fetchone()
    loaded = repo.get_memory_vectors([memory_id])

    assert legacy_table is None
    assert np.array_equal(loaded[memory_id], vector)


def test_old_rows_keep_all_new_columns_null_without_backfill(manager):
    _repo_with_legacy_memories(manager)
    manager.execute_write(
        """INSERT INTO memories(group_id, content, timestamp, source)
           VALUES (?, ?, ?, ?)""",
        ("legacy-group", "immutable legacy row", 123.0, "legacy"),
    )
    manager.commit()

    ensure_memories_v2_schema(manager)

    row = manager.execute_read(
        """SELECT bot_id, session_id, visibility, origin_fingerprint, provenance,
                  version, quarantine, resolution_state
           FROM memories WHERE group_id='legacy-group'"""
    ).fetchone()
    assert tuple(row) == (None,) * len(V2_COLUMNS)


def test_partial_schema_adds_only_missing_columns_without_touching_rows(manager):
    _repo_with_legacy_memories(manager)
    manager.execute_write("ALTER TABLE memories ADD COLUMN bot_id TEXT")
    manager.execute_write("ALTER TABLE memories ADD COLUMN version INTEGER")
    manager.execute_write(
        "INSERT INTO memories(group_id, content, timestamp) VALUES (?, ?, ?)",
        ("partial-group", "partial legacy row", 456.0),
    )
    manager.commit()

    ensure_memories_v2_schema(manager)

    assert V2_COLUMNS <= _columns(manager)
    row = manager.execute_read(
        "SELECT bot_id, version, quarantine, resolution_state FROM memories WHERE group_id=?",
        ("partial-group",),
    ).fetchone()
    assert tuple(row) == (None, None, None, None)


def test_repeated_migration_is_idempotent(manager):
    _repo_with_legacy_memories(manager)

    ensure_memories_v2_schema(manager)
    first_columns = _columns(manager)
    first_indexes = manager.execute_read("PRAGMA index_list(memories)").fetchall()
    ensure_memories_v2_schema(manager)

    assert _columns(manager) == first_columns
    assert manager.execute_read("PRAGMA index_list(memories)").fetchall() == first_indexes


def test_scope_write_persists_v2_provenance_and_quarantine(manager):
    repo = _repo_with_legacy_memories(manager)
    ensure_memories_v2_schema(manager)
    scope = _group_scope()

    memory_id = repo.add_memory(
        "group-1",
        "scoped memory",
        sender_id="qq:user:10001",
        sender_name="Tester",
        timestamp=789.0,
        source="unit",
        scope=scope,
        provenance={"writer": "pytest"},
        origin_metadata={"event_id": "evt-1"},
        quarantine=True,
    )

    row = manager.execute_read(
        """SELECT bot_id, session_id, visibility, origin_fingerprint, provenance,
                  version, quarantine, resolution_state
           FROM memories WHERE id=?""",
        (memory_id,),
    ).fetchone()
    assert row[0:3] == ("bot-alpha", "qq:group:group-1", "group")
    assert len(row[3]) == 64
    assert int(row[5]) == MEMORIES_V2_VERSION
    assert int(row[6]) == 1
    assert row[7] == "resolved"
    provenance = json.loads(row[4])
    assert provenance["version"] == MEMORIES_V2_VERSION
    assert provenance["fingerprint_algorithm"] == "sha256"
    assert provenance["origin_fingerprint"] == row[3]
    assert provenance["metadata"] == {"writer": "pytest"}
    origin_payload = json.loads(manager.execute_read(
        "SELECT provenance FROM memories WHERE id=?", (memory_id,)
    ).fetchone()[0])
    assert origin_payload["version"] == 1


def test_scope_write_rejects_missing_or_noncanonical_scope(manager):
    repo = _repo_with_legacy_memories(manager)
    ensure_memories_v2_schema(manager)
    scope = _group_scope()

    with pytest.raises(MemoryScopeError, match="RuntimeScope") as missing:
        repo.add_memory("group-1", "scope required")
    assert missing.value.reason_code == "scope_required"
    with pytest.raises(MemoryScopeError, match="canonical group id") as mismatch:
        repo.add_memory("another-group", "mismatched group", scope=scope)
    assert mismatch.value.reason_code == "scope_session_mismatch"


def test_scoped_hnsw_post_filter_rejects_cross_scope_and_quarantined_rows(manager):
    repo = _repo_with_legacy_memories(manager)
    ensure_memories_v2_schema(manager)
    alpha = _group_scope(bot_id="bot-alpha")
    beta = _group_scope(bot_id="bot-beta")

    alpha_id = repo.add_memory("group-1", "alpha", scope=alpha)
    beta_id = repo.add_memory("group-1", "beta", scope=beta)
    quarantined_id = repo.add_memory("group-1", "quarantined", scope=alpha, quarantine=True)

    rows = repo.get_memories_by_ids([alpha_id, beta_id, quarantined_id], scope=alpha)

    assert [row["id"] for row in rows] == [alpha_id]
    assert repo.get_all_memory_vectors() == []


def test_scoped_duplicate_lookup_cannot_hit_cross_bot_or_legacy_null_rows(manager):
    repo = _repo_with_legacy_memories(manager)
    manager.execute_write(
        "INSERT INTO memories(group_id, content, timestamp) VALUES (?, ?, ?)",
        ("group-1", "same normalized content", 1000.0),
    )
    manager.commit()
    ensure_memories_v2_schema(manager)
    alpha = _group_scope(bot_id="bot-alpha")
    beta = _group_scope(bot_id="bot-beta")

    beta_id = repo.add_memory("group-1", "same normalized content", scope=beta, timestamp=1001.0)

    assert repo.find_recent_duplicate_memory(
        scope=alpha,
        normalized_content="same normalized content",
        since_ts=900.0,
    ) is None
    assert repo.find_recent_duplicate_memory(
        scope=beta,
        normalized_content="same normalized content",
        since_ts=900.0,
    ) == beta_id


def test_hnsw_post_filter_keeps_same_group_legacy_rows_without_cross_bot_leakage(manager):
    repo = _repo_with_legacy_memories(manager)
    manager.execute_write(
        "INSERT INTO memories(group_id, content, timestamp) VALUES (?, ?, ?)",
        ("group-1", "legacy same group", 1000.0),
    )
    manager.commit()
    ensure_memories_v2_schema(manager)
    alpha = _group_scope(bot_id="bot-alpha")
    beta = _group_scope(bot_id="bot-beta")
    alpha_id = repo.add_memory("group-1", "formal alpha", scope=alpha)
    beta_id = repo.add_memory("group-1", "formal beta", scope=beta)
    legacy_id = manager.execute_read(
        "SELECT id FROM memories WHERE content='legacy same group'"
    ).fetchone()[0]
    # Old runtime data uses evicted to mean "not resident in the previous HNSW".
    # It remains eligible in the legacy compatibility lane, unlike scoped rows.
    manager.execute_write("UPDATE memories SET memory_type='evicted' WHERE id=?", (legacy_id,))
    manager.commit()

    rows = repo.get_memories_by_ids([legacy_id, alpha_id, beta_id], scope=alpha)

    assert {row["id"] for row in rows} == {legacy_id, alpha_id}
    assert next(row for row in rows if row["id"] == legacy_id)["_tag_lane"] == "legacy"


def test_legacy_tag_cold_candidates_stay_group_scoped_and_allow_explicit_global_reads(manager):
    repo = _repo_with_legacy_memories(manager)
    vector = np.asarray([0.1, 0.2], dtype=np.float32).tobytes()
    manager.execute_write(
        "INSERT INTO memories(group_id, content, vector, timestamp) VALUES (?, ?, ?, ?)",
        ("group-1", "legacy group one", vector, 1000.0),
    )
    manager.execute_write(
        "INSERT INTO memories(group_id, content, vector, timestamp) VALUES (?, ?, ?, ?)",
        ("group-2", "legacy group two", vector, 1001.0),
    )
    manager.execute_write("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, vector BLOB)")
    manager.execute_write("INSERT INTO tags VALUES (1, 'legacy topic', ?)", (vector,))
    manager.execute_write("INSERT INTO memory_tags(memory_id, tag_id, relevance) VALUES (1, 1, 0.8)")
    manager.execute_write("INSERT INTO memory_tags(memory_id, tag_id, relevance) VALUES (2, 1, 1.0)")
    manager.commit()
    ensure_memories_v2_schema(manager)

    scoped = repo.list_legacy_cold_memory_candidates(_group_scope(group_id="group-1"), [1], limit=10)
    global_denied = repo.list_legacy_cold_memory_candidates(None, [1], limit=10)
    global_allowed = repo.list_legacy_cold_memory_candidates(None, [1], limit=10, allow_unscoped=True)

    assert [row["content"] for row in scoped] == ["legacy group one"]
    assert global_denied == []
    assert {row["content"] for row in global_allowed} == {"legacy group one", "legacy group two"}
