import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import govern_wave_memory_storage as governance
from engine.index_manifest import IndexManifest, checksum_file, generation_path, manifest_path


def _vector(dimension: int, value: float) -> bytes:
    return np.full(dimension, value, dtype=np.float32).tobytes()


def _create_dirty_database(path: Path, dimension: int = 4) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(
            """
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY,
                content TEXT NOT NULL,
                bot_id TEXT,
                session_id TEXT,
                resolution_state TEXT
            );
            CREATE TABLE tags (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                vector BLOB,
                parent_id INTEGER,
                frequency INTEGER DEFAULT 0,
                is_core INTEGER DEFAULT 0,
                FOREIGN KEY (parent_id) REFERENCES tags(id) ON DELETE SET NULL
            );
            CREATE TABLE memory_tags (
                memory_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (memory_id, tag_id),
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE tag_extraction_status (
                memory_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE tag_intrinsic_residuals (
                tag_id INTEGER PRIMARY KEY,
                residual_energy REAL NOT NULL,
                computed_at REAL NOT NULL,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE tag_relations (
                id INTEGER PRIMARY KEY,
                source_tag_id INTEGER NOT NULL,
                target_tag_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                FOREIGN KEY (source_tag_id) REFERENCES tags(id) ON DELETE CASCADE,
                FOREIGN KEY (target_tag_id) REFERENCES tags(id) ON DELETE CASCADE
            );
            CREATE TABLE tag_pair_similarity (
                tag_id_a INTEGER NOT NULL,
                tag_id_b INTEGER NOT NULL,
                similarity REAL NOT NULL,
                PRIMARY KEY (tag_id_a, tag_id_b)
            );
            CREATE TABLE facts (
                id INTEGER PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                source_memory_id INTEGER,
                FOREIGN KEY (source_memory_id) REFERENCES memories(id) ON DELETE SET NULL
            );
            CREATE TABLE memory_vectors (
                memory_id INTEGER PRIMARY KEY,
                vector BLOB NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );
            CREATE TABLE write_operations (
                operation_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                write_sequence INTEGER NOT NULL UNIQUE
            );
            """
        )
        conn.executemany(
            "INSERT INTO memories(id, content, bot_id, session_id, resolution_state) VALUES (?, ?, ?, ?, ?)",
            [
                (1, "one", "bot-a", "group-1", "resolved"),
                (2, "two", "bot-a", "group-1", "resolved"),
                (3, "three", "bot-b", "group-2", "unresolved"),
            ],
        )
        conn.executemany(
            "INSERT INTO tags(id, name, vector, parent_id, frequency, is_core) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, "used", _vector(dimension, 1.0), None, 99, 0),
                (2, "unused", _vector(dimension, 2.0), None, 9, 0),
                (3, "core", _vector(dimension, 3.0), None, 7, 1),
                (4, "related", None, None, 4, 0),
                (5, "parent", None, None, 5, 0),
                (6, "child", None, 5, 6, 0),
                (7, "invalid-link-only", None, None, 7, 0),
                (8, "orphan-relation-only", None, None, 8, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO memory_tags(memory_id, tag_id) VALUES (?, ?)",
            [(1, 1), (2, 1), (99, 1), (1, 999), (99, 999), (99, 7)],
        )
        conn.executemany(
            "INSERT INTO tag_extraction_status(memory_id, status) VALUES (?, ?)",
            [(1, "done"), (99, "done")],
        )
        conn.executemany(
            "INSERT INTO tag_intrinsic_residuals(tag_id, residual_energy, computed_at) VALUES (?, ?, ?)",
            [(1, 0.1, 1.0), (999, 0.9, 1.0)],
        )
        conn.executemany(
            "INSERT INTO tag_relations(id, source_tag_id, target_tag_id, relation_type) VALUES (?, ?, ?, ?)",
            [(1, 4, 1, "related"), (2, 8, 999, "broken")],
        )
        conn.executemany(
            "INSERT INTO tag_pair_similarity(tag_id_a, tag_id_b, similarity) VALUES (?, ?, ?)",
            [(1, 2, 0.2), (1, 4, 0.4), (7, 1, 0.7), (5, 6, 0.6)],
        )
        conn.executemany(
            "INSERT INTO facts(id, subject, predicate, object, source_memory_id) VALUES (?, ?, ?, ?, ?)",
            [(1, "a", "is", "valid", 1), (2, "b", "is", "orphan", 99)],
        )
        conn.executemany(
            "INSERT INTO memory_vectors(memory_id, vector) VALUES (?, ?)",
            [(1, _vector(dimension, 1.0)), (2, _vector(dimension, 2.0))],
        )
        conn.executemany(
            "INSERT INTO write_operations(operation_id, status, write_sequence) VALUES (?, ?, ?)",
            [("committed-1", "committed", 7), ("pending-1", "pending", 9)],
        )
        conn.commit()
    finally:
        conn.close()


def _fake_index_builder(
    candidate_db: Path,
    runtime_index_path: Path,
    dimension: int,
    staging_dir: Path,
) -> governance.TagIndexArtifact:
    conn = sqlite3.connect(candidate_db)
    try:
        count = int(
            conn.execute("SELECT COUNT(*) FROM tags WHERE vector IS NOT NULL").fetchone()[0]
        )
        watermark = governance._committed_write_sequence_watermark(conn)
    finally:
        conn.close()

    staging_base = staging_dir / runtime_index_path.name
    stage_generation = generation_path(staging_base, 1)
    stage_generation.write_bytes(
        json.dumps({"count": count, "dimension": dimension}, sort_keys=True).encode("ascii")
    )
    manifest = IndexManifest(
        kind="tag",
        generation=1,
        dimension=dimension,
        db_watermark=watermark,
        count=count,
        checksum=checksum_file(stage_generation),
        created_at="2026-01-01T00:00:00+00:00",
    )
    stage_manifest = manifest_path(staging_base)
    stage_manifest.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return governance.TagIndexArtifact(stage_generation, stage_manifest, manifest)


def _write_existing_index(index_path: Path, dimension: int = 4) -> bytes:
    old_generation = generation_path(index_path, 1)
    old_generation.write_bytes(b"old-generation")
    old_manifest = IndexManifest(
        kind="tag",
        generation=1,
        dimension=dimension,
        db_watermark=1,
        count=0,
        checksum=checksum_file(old_generation),
        created_at="2025-01-01T00:00:00+00:00",
    )
    payload = (json.dumps(old_manifest.to_dict(), sort_keys=True) + "\n").encode("utf-8")
    manifest_path(index_path).write_bytes(payload)
    return payload


def test_candidate_governance_preserves_memories_and_cleans_storage(tmp_path):
    source = tmp_path / "wave_memory.db"
    candidate = tmp_path / "candidate.db"
    _create_dirty_database(source)
    before = governance.analyze_database(source)["memory_invariants"]

    governance.create_candidate_database(source, candidate)
    changes = governance.govern_candidate_database(candidate)
    acceptance = governance.validate_candidate_database(candidate, before)

    assert changes == {
        "memory_tags_deleted": 4,
        "tag_extraction_status_deleted": 1,
        "tag_intrinsic_residuals_deleted": 1,
        "tag_relations_deleted": 1,
        "facts_source_memory_id_nulled": 1,
        "tags_deleted": 4,
        "tag_pair_similarity_deleted": 3,
        "memory_vectors_rows_removed": 2,
    }
    assert acceptance["quick_check"] == "ok"
    assert acceptance["foreign_key_violations"] == 0
    assert acceptance["memory_invariants"] == before
    assert acceptance["memory_vectors_exists"] is False

    conn = sqlite3.connect(candidate)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 3
        assert conn.execute(
            """SELECT bot_id, session_id, resolution_state, COUNT(*)
                 FROM memories
                GROUP BY bot_id, session_id, resolution_state
                ORDER BY bot_id, session_id, resolution_state"""
        ).fetchall() == [
            ("bot-a", "group-1", "resolved", 2),
            ("bot-b", "group-2", "unresolved", 1),
        ]
        assert conn.execute("SELECT memory_id, tag_id FROM memory_tags ORDER BY memory_id").fetchall() == [
            (1, 1),
            (2, 1),
        ]
        assert conn.execute("SELECT memory_id FROM tag_extraction_status").fetchall() == [(1,)]
        assert conn.execute("SELECT tag_id FROM tag_intrinsic_residuals").fetchall() == [(1,)]
        assert conn.execute(
            "SELECT source_tag_id, target_tag_id FROM tag_relations"
        ).fetchall() == [(4, 1)]
        assert conn.execute(
            "SELECT id, source_memory_id FROM facts ORDER BY id"
        ).fetchall() == [(1, 1), (2, None)]
        assert conn.execute("SELECT id FROM tags ORDER BY id").fetchall() == [
            (1,),
            (3,),
            (4,),
            (5,),
        ]
        assert conn.execute("SELECT frequency FROM tags WHERE id=1").fetchone()[0] == 2
        assert conn.execute(
            "SELECT tag_id_a, tag_id_b FROM tag_pair_similarity"
        ).fetchall() == [(1, 4)]
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_vectors'"
        ).fetchone() is None
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_storage_manifest_watermark_uses_only_committed_write_sequence(tmp_path):
    database_path = tmp_path / "wave_memory.db"
    _create_dirty_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        assert governance._committed_write_sequence_watermark(connection) == 7
    finally:
        connection.close()


def test_real_tag_index_builder_writes_reloadable_generation_when_hnsw_available(
    tmp_path,
):
    pytest.importorskip("hnswlib")
    source = tmp_path / "wave_memory.db"
    candidate = tmp_path / "candidate.db"
    index_path = tmp_path / "tags.hnsw"
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    _create_dirty_database(source)
    governance.create_candidate_database(source, candidate)
    governance.govern_candidate_database(candidate)

    artifact = governance.build_staging_tag_index(
        candidate,
        index_path,
        4,
        staging_dir,
    )

    assert artifact.generation_path.is_file()
    assert artifact.manifest_path.is_file()
    assert artifact.manifest.kind == "tag"
    assert artifact.manifest.dimension == 4
    assert artifact.manifest.count == 2
    assert artifact.manifest.db_watermark == 7
    assert checksum_file(artifact.generation_path) == artifact.manifest.checksum


def test_default_is_read_only_and_apply_requires_explicit_confirmation(tmp_path):
    db_path = tmp_path / "wave_memory.db"
    config_path = tmp_path / "config.json"
    _create_dirty_database(db_path)
    config_path.write_text("{}", encoding="utf-8")
    original = db_path.read_bytes()

    dry_run = governance.govern_storage(db_path, dimension=4)
    refused = governance.govern_storage(
        db_path,
        dimension=4,
        config_path=config_path,
        apply=True,
        runtime_stopped_confirmed=False,
        index_builder=_fake_index_builder,
    )

    assert dry_run["status"] == "dry-run"
    assert refused["status"] == "refused"
    assert "runtime-stopped-confirmed" in refused["error"]
    assert db_path.read_bytes() == original
    assert not (tmp_path / "backups").exists()


def test_writer_lock_refuses_an_existing_runtime_lease(tmp_path):
    db_path = tmp_path / "wave_memory.db"
    _create_dirty_database(db_path)
    lock_path = db_path.with_name(f"{db_path.name}.writer.lock")

    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(governance.LockUnavailableError, match="writer lock"):
                with governance._writer_lock(db_path):
                    pytest.fail("held runtime lease must never be bypassed")
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_apply_refuses_tag_only_governance_without_memory_index_artifact(tmp_path):
    db_path = tmp_path / "wave_memory.db"
    config_path = tmp_path / "config.json"
    index_path = tmp_path / "tags.hnsw"
    _create_dirty_database(db_path)
    config_path.write_text('{"backup_max_count": 5}', encoding="utf-8")
    original_db = db_path.read_bytes()
    original_manifest = _write_existing_index(index_path)

    result = governance.govern_storage(
        db_path,
        tag_index_path=index_path,
        dimension=4,
        config_path=config_path,
        apply=True,
        runtime_stopped_confirmed=True,
        index_builder=_fake_index_builder,
    )

    assert result["status"] == "refused"
    assert "memory index artifact is required" in result["error"]
    assert "tag-only" in result["error"]
    assert result["backup"] is None
    assert result["candidate_changes"] is None
    assert db_path.read_bytes() == original_db
    assert manifest_path(index_path).read_bytes() == original_manifest
    assert json.loads(config_path.read_text(encoding="utf-8"))["backup_max_count"] == 5
    assert not (tmp_path / "backups").exists()
    assert not generation_path(index_path, 2).exists()


