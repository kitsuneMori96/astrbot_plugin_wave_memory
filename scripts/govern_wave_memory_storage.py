"""Offline, rollback-safe storage governance for WaveMemory.

The default mode is a read-only dry run. Apply requests fail closed until both
memory and tag HNSW artifacts can be staged and switched with the governed SQLite
candidate; the existing tag-only workflow is retained only as unreachable groundwork.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.index_manifest import (  # noqa: E402
    IndexManifest,
    checksum_file,
    generation_path,
    latest_generation,
    manifest_path,
    read_index_manifest,
)

SIDECAR_SUFFIXES = ("-wal", "-shm")
SCOPE_COLUMNS = ("bot_id", "session_id", "resolution_state")


class GovernanceError(RuntimeError):
    """Raised when a governance precondition or acceptance gate fails."""


class LockUnavailableError(GovernanceError):
    """Raised when the runtime writer lease cannot be acquired safely."""


@dataclass(frozen=True)
class TagIndexArtifact:
    """A validated staging HNSW generation and its staging manifest."""

    generation_path: Path
    manifest_path: Path
    manifest: IndexManifest


IndexBuilder = Callable[[Path, Path, int, Path], TagIndexArtifact]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _execute_count(conn: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    cursor = conn.execute(sql, parameters)
    return max(0, int(cursor.rowcount))


def _memory_invariants(conn: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(conn, "memories"):
        raise GovernanceError("required table is missing: memories")
    missing = set(SCOPE_COLUMNS) - _columns(conn, "memories")
    if missing:
        raise GovernanceError(
            "memories scope columns are missing: " + ", ".join(sorted(missing))
        )
    rows = conn.execute(
        """SELECT bot_id, session_id, resolution_state, COUNT(*)
             FROM memories
            GROUP BY bot_id, session_id, resolution_state
            ORDER BY bot_id, session_id, resolution_state"""
    ).fetchall()
    return {
        "count": int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]),
        "scope_distribution": [
            {
                "bot_id": row[0],
                "session_id": row[1],
                "resolution_state": row[2],
                "count": int(row[3]),
            }
            for row in rows
        ],
    }


def _count_if_table(conn: sqlite3.Connection, table: str, sql: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(sql).fetchone()[0])


def _tag_pair_columns(conn: sqlite3.Connection) -> tuple[str, str] | None:
    columns = _columns(conn, "tag_pair_similarity")
    if {"tag_id_a", "tag_id_b"} <= columns:
        return "tag_id_a", "tag_id_b"
    if {"tag_a", "tag_b"} <= columns:
        return "tag_a", "tag_b"
    return None


def analyze_database(db_path: str | Path) -> dict[str, Any]:
    """Read-only analysis of the rows that candidate governance would change."""
    path = Path(db_path)
    conn = _connect_readonly(path)
    try:
        invariants = _memory_invariants(conn)
        report: dict[str, Any] = {
            "memory_invariants": invariants,
            "orphan_memory_tags": _count_if_table(
                conn,
                "memory_tags",
                """SELECT COUNT(*) FROM memory_tags mt
                     WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=mt.memory_id)
                        OR NOT EXISTS (SELECT 1 FROM tags t WHERE t.id=mt.tag_id)""",
            ),
            "orphan_tag_extraction_status": _count_if_table(
                conn,
                "tag_extraction_status",
                """SELECT COUNT(*) FROM tag_extraction_status tes
                     WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=tes.memory_id)""",
            ),
            "orphan_tag_intrinsic_residuals": _count_if_table(
                conn,
                "tag_intrinsic_residuals",
                """SELECT COUNT(*) FROM tag_intrinsic_residuals tir
                     WHERE NOT EXISTS (SELECT 1 FROM tags t WHERE t.id=tir.tag_id)""",
            ),
            "orphan_tag_relations": _count_if_table(
                conn,
                "tag_relations",
                """SELECT COUNT(*) FROM tag_relations tr
                     WHERE NOT EXISTS (SELECT 1 FROM tags s WHERE s.id=tr.source_tag_id)
                        OR NOT EXISTS (SELECT 1 FROM tags t WHERE t.id=tr.target_tag_id)""",
            ),
            "facts_to_null": (
                int(
                    conn.execute(
                        """SELECT COUNT(*) FROM facts f
                             WHERE f.source_memory_id IS NOT NULL
                               AND NOT EXISTS (
                                   SELECT 1 FROM memories m
                                   WHERE m.id=f.source_memory_id
                               )"""
                    ).fetchone()[0]
                )
                if _table_exists(conn, "facts")
                and "source_memory_id" in _columns(conn, "facts")
                else 0
            ),
            "strict_unused_tags": _count_if_table(
                conn,
                "tags",
                """SELECT COUNT(*) FROM tags t
                     WHERE COALESCE(t.is_core, 0)=0
                       AND NOT EXISTS (
                           SELECT 1 FROM memory_tags mt
                           JOIN memories m ON m.id=mt.memory_id
                           WHERE mt.tag_id=t.id
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM tag_relations tr
                           WHERE tr.source_tag_id=t.id OR tr.target_tag_id=t.id
                       )
                       AND NOT EXISTS (SELECT 1 FROM tags child WHERE child.parent_id=t.id)""",
            ),
            "memory_vectors_rows": _count_if_table(
                conn,
                "memory_vectors",
                "SELECT COUNT(*) FROM memory_vectors",
            ),
        }
        return report
    finally:
        conn.close()


def create_candidate_database(source_path: str | Path, candidate_path: str | Path) -> None:
    """Copy a coherent source snapshot with the SQLite Backup API."""
    source = Path(source_path)
    candidate = Path(candidate_path)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    src = _connect_readonly(source)
    try:
        dst = sqlite3.connect(candidate)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def govern_candidate_database(candidate_path: str | Path) -> dict[str, int]:
    """Govern only a staging candidate database, never the runtime database."""
    path = Path(candidate_path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        required = {"memories", "tags", "memory_tags"}
        missing = sorted(table for table in required if not _table_exists(conn, table))
        if missing:
            raise GovernanceError("required tables are missing: " + ", ".join(missing))

        changes: dict[str, int] = {}
        conn.execute("BEGIN IMMEDIATE")
        try:
            changes["memory_tags_deleted"] = _execute_count(
                conn,
                """DELETE FROM memory_tags
                     WHERE NOT EXISTS (
                               SELECT 1 FROM memories m WHERE m.id=memory_tags.memory_id
                           )
                        OR NOT EXISTS (
                               SELECT 1 FROM tags t WHERE t.id=memory_tags.tag_id
                           )""",
            )
            changes["tag_extraction_status_deleted"] = (
                _execute_count(
                    conn,
                    """DELETE FROM tag_extraction_status
                         WHERE NOT EXISTS (
                             SELECT 1 FROM memories m
                             WHERE m.id=tag_extraction_status.memory_id
                         )""",
                )
                if _table_exists(conn, "tag_extraction_status")
                else 0
            )
            changes["tag_intrinsic_residuals_deleted"] = (
                _execute_count(
                    conn,
                    """DELETE FROM tag_intrinsic_residuals
                         WHERE NOT EXISTS (
                             SELECT 1 FROM tags t
                             WHERE t.id=tag_intrinsic_residuals.tag_id
                         )""",
                )
                if _table_exists(conn, "tag_intrinsic_residuals")
                else 0
            )
            changes["tag_relations_deleted"] = (
                _execute_count(
                    conn,
                    """DELETE FROM tag_relations
                         WHERE NOT EXISTS (
                                   SELECT 1 FROM tags s
                                   WHERE s.id=tag_relations.source_tag_id
                               )
                            OR NOT EXISTS (
                                   SELECT 1 FROM tags t
                                   WHERE t.id=tag_relations.target_tag_id
                               )""",
                )
                if _table_exists(conn, "tag_relations")
                else 0
            )
            changes["facts_source_memory_id_nulled"] = (
                _execute_count(
                    conn,
                    """UPDATE facts SET source_memory_id=NULL
                         WHERE source_memory_id IS NOT NULL
                           AND NOT EXISTS (
                               SELECT 1 FROM memories m
                               WHERE m.id=facts.source_memory_id
                           )""",
                )
                if _table_exists(conn, "facts")
                and "source_memory_id" in _columns(conn, "facts")
                else 0
            )
            changes["tags_deleted"] = _execute_count(
                conn,
                """DELETE FROM tags
                     WHERE COALESCE(is_core, 0)=0
                       AND NOT EXISTS (
                           SELECT 1 FROM memory_tags mt WHERE mt.tag_id=tags.id
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM tag_relations tr
                           WHERE tr.source_tag_id=tags.id OR tr.target_tag_id=tags.id
                       )
                       AND NOT EXISTS (
                           SELECT 1 FROM tags child WHERE child.parent_id=tags.id
                       )""",
            )
            pair_columns = _tag_pair_columns(conn)
            changes["tag_pair_similarity_deleted"] = (
                _execute_count(
                    conn,
                    f"""DELETE FROM tag_pair_similarity
                         WHERE NOT EXISTS (
                                   SELECT 1 FROM tags a
                                   WHERE a.id=tag_pair_similarity.{pair_columns[0]}
                               )
                            OR NOT EXISTS (
                                   SELECT 1 FROM tags b
                                   WHERE b.id=tag_pair_similarity.{pair_columns[1]}
                               )""",
                )
                if pair_columns is not None
                else 0
            )
            conn.execute(
                """UPDATE tags
                      SET frequency=(
                          SELECT COUNT(*) FROM memory_tags mt WHERE mt.tag_id=tags.id
                      )"""
            )
            if _table_exists(conn, "memory_vectors"):
                changes["memory_vectors_rows_removed"] = int(
                    conn.execute("SELECT COUNT(*) FROM memory_vectors").fetchone()[0]
                )
                conn.execute("DROP TABLE memory_vectors")
            else:
                changes["memory_vectors_rows_removed"] = 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        conn.execute("VACUUM")
        return changes
    finally:
        conn.close()


def validate_candidate_database(
    candidate_path: str | Path,
    expected_invariants: dict[str, Any],
) -> dict[str, Any]:
    """Run all mandatory candidate acceptance gates."""
    path = Path(candidate_path)
    conn = _connect_readonly(path)
    try:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        quick_check = "ok" if quick_rows == ["ok"] else quick_rows
        foreign_key_rows = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
        actual_invariants = _memory_invariants(conn)
        vectors_exists = _table_exists(conn, "memory_vectors")
        acceptance = {
            "quick_check": quick_check,
            "foreign_key_violations": len(foreign_key_rows),
            "memory_invariants": actual_invariants,
            "memory_vectors_exists": vectors_exists,
        }
        failures = []
        if quick_check != "ok":
            failures.append(f"quick_check={quick_check!r}")
        if foreign_key_rows:
            failures.append(f"foreign_key_check={len(foreign_key_rows)}")
        if actual_invariants != expected_invariants:
            failures.append("memory count or scope distribution changed")
        if vectors_exists:
            failures.append("memory_vectors still exists")
        if failures:
            raise GovernanceError("candidate acceptance failed: " + "; ".join(failures))
        return acceptance
    finally:
        conn.close()


def _tag_vector_count(db_path: Path) -> int:
    conn = _connect_readonly(db_path)
    try:
        return int(
            conn.execute("SELECT COUNT(*) FROM tags WHERE vector IS NOT NULL").fetchone()[0]
        )
    finally:
        conn.close()


def _committed_write_sequence_watermark(conn: sqlite3.Connection) -> int:
    """Return the canonical committed-write watermark used by every HNSW manifest."""
    if not _table_exists(conn, "write_operations"):
        return 0
    columns = _columns(conn, "write_operations")
    required = {"status", "write_sequence"}
    if not required <= columns:
        raise GovernanceError(
            "write_operations watermark columns are missing: "
            + ", ".join(sorted(required - columns))
        )
    return int(
        conn.execute(
            "SELECT COALESCE(MAX(write_sequence), 0) "
            "FROM write_operations WHERE status='committed'"
        ).fetchone()[0]
    )


def build_staging_tag_index(
    candidate_db: Path,
    runtime_index_path: Path,
    dimension: int,
    staging_dir: Path,
) -> TagIndexArtifact:
    """Build and reload-verify a staging tag HNSW generation and manifest."""
    import numpy as np

    from engine.vector_index import VectorIndex

    staging_base = staging_dir / runtime_index_path.name
    conn = _connect_readonly(candidate_db)
    try:
        rows = conn.execute(
            "SELECT id, vector FROM tags WHERE vector IS NOT NULL ORDER BY id"
        ).fetchall()
        watermark = _committed_write_sequence_watermark(conn)
    finally:
        conn.close()

    expected_bytes = dimension * 4
    bad_ids = [int(row[0]) for row in rows if len(row[1]) != expected_bytes]
    if bad_ids:
        preview = ", ".join(str(tag_id) for tag_id in bad_ids[:10])
        raise GovernanceError(
            f"tag vectors do not match dimension {dimension}: tag ids {preview}"
        )

    index = VectorIndex(
        dimension=dimension,
        max_elements=max(50000, len(rows) + 1),
        index_path=str(staging_base),
        kind="tag",
    )
    if rows:
        vectors = np.asarray(
            [np.frombuffer(row[1], dtype=np.float32) for row in rows],
            dtype=np.float32,
        )
        index.add([int(row[0]) for row in rows], vectors)
    manifest = index.save(db_watermark=watermark)
    if manifest is None:
        raise GovernanceError("tag index did not produce a staging manifest")

    committed = read_index_manifest(
        staging_base,
        expected_kind="tag",
        expected_dimension=dimension,
    )
    if committed is None:
        raise GovernanceError("staging tag index manifest is missing")
    reloaded = VectorIndex(
        dimension=dimension,
        max_elements=max(50000, len(rows) + 1),
        index_path=str(staging_base),
        kind="tag",
    )
    stage_generation = generation_path(staging_base, committed.generation)
    if reloaded.dimension != dimension:
        raise GovernanceError("reloaded tag index dimension mismatch")
    if reloaded.count != len(rows) or committed.count != len(rows):
        raise GovernanceError("reloaded tag index count mismatch")
    if checksum_file(stage_generation) != committed.checksum:
        raise GovernanceError("reloaded tag index checksum mismatch")
    return TagIndexArtifact(
        generation_path=stage_generation,
        manifest_path=manifest_path(staging_base),
        manifest=committed,
    )


def _validate_index_artifact(
    artifact: TagIndexArtifact,
    candidate_db: Path,
    dimension: int,
) -> None:
    if not artifact.generation_path.is_file() or not artifact.manifest_path.is_file():
        raise GovernanceError("staging tag index artifact is incomplete")
    if artifact.manifest.kind != "tag":
        raise GovernanceError("staging tag index kind mismatch")
    if artifact.manifest.dimension != dimension:
        raise GovernanceError("staging tag index dimension mismatch")
    expected_count = _tag_vector_count(candidate_db)
    if artifact.manifest.count != expected_count:
        raise GovernanceError(
            f"staging tag index count mismatch: expected {expected_count}, "
            f"got {artifact.manifest.count}"
        )
    actual_checksum = checksum_file(artifact.generation_path)
    if actual_checksum != artifact.manifest.checksum:
        raise GovernanceError("staging tag index checksum mismatch")


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _manifest_bytes(manifest: IndexManifest) -> bytes:
    text = json.dumps(
        manifest.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (text + "\n").encode("utf-8")


def _restore_bytes(path: Path, original: bytes | None) -> None:
    if original is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    _atomic_write_bytes(path, original)


def _update_config(config_path: Path) -> bytes:
    original = config_path.read_bytes()
    had_bom = original.startswith(b"\xef\xbb\xbf")
    try:
        payload = json.loads(original.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GovernanceError(f"cannot read config {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise GovernanceError("config root must be a JSON object")
    payload["backup_max_count"] = 1
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    if had_bom:
        encoded = b"\xef\xbb\xbf" + encoded
    _atomic_write_bytes(config_path, encoded + b"\n")
    return original


def _checkpoint_runtime_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is not None and int(row[0]) != 0:
            raise GovernanceError(f"WAL checkpoint was busy: {tuple(row)}")
    finally:
        conn.close()


def _remove_sidecars(db_path: Path) -> None:
    for suffix in SIDECAR_SUFFIXES:
        with contextlib.suppress(FileNotFoundError):
            Path(f"{db_path}{suffix}").unlink()


def _unique_backup_path(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = backup_dir / f"wave_memory_{stamp}.db"
    candidate = base
    counter = 1
    while candidate.exists():
        candidate = backup_dir / f"wave_memory_{stamp}_{counter:02d}.db"
        counter += 1
    return candidate


@contextlib.contextmanager
def _writer_lock(db_path: Path) -> Iterator[Path]:
    """Acquire the same one-byte writer lease used by the runtime on Windows."""
    lock_path = db_path.with_name(f"{db_path.name}.writer.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                raise LockUnavailableError(
                    f"cannot acquire non-blocking exclusive writer lock: {lock_path}"
                ) from exc
        elif os.name == "posix":
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                raise LockUnavailableError(
                    f"cannot acquire non-blocking exclusive writer lock: {lock_path}"
                ) from exc
        else:
            raise LockUnavailableError(
                f"writer lock is unsupported on platform {os.name!r}; refusing apply"
            )
        yield lock_path
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


# Backward-compatible internal alias for callers/tests from the Linux-only implementation.
_linux_writer_lock = _writer_lock


def govern_storage(
    db_path: str | Path,
    *,
    tag_index_path: str | Path | None = None,
    dimension: int = 1024,
    config_path: str | Path | None = None,
    apply: bool = False,
    runtime_stopped_confirmed: bool = False,
    index_builder: IndexBuilder | None = None,
) -> dict[str, Any]:
    """Analyze storage or refuse unsafe tag-only apply requests."""
    db = Path(db_path)
    tag_index = Path(tag_index_path) if tag_index_path is not None else db.with_name("tags.hnsw")
    config = Path(config_path) if config_path is not None else None
    report: dict[str, Any] = {
        "status": "failed",
        "mode": "apply" if apply else "dry-run",
        "db": str(db),
        "tag_index": str(tag_index),
        "dimension": dimension,
        "config": str(config) if config is not None else None,
        "backup": None,
        "analysis": None,
        "candidate_changes": None,
        "acceptance": None,
        "tag_index_manifest": None,
        "error": "",
    }

    try:
        if not db.is_file():
            raise GovernanceError(f"database does not exist: {db}")
        if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
            raise GovernanceError("dimension must be a positive integer")
        report["analysis"] = analyze_database(db)
        if not apply:
            report["status"] = "dry-run"
            return report
        if not runtime_stopped_confirmed:
            report["status"] = "refused"
            raise GovernanceError("--apply requires --runtime-stopped-confirmed")

        # The current workflow only stages the tag HNSW. Switching the governed DB
        # without a matching memory HNSW generation would publish inconsistent
        # derived state, so fail closed until the dual-index transaction exists.
        report["status"] = "refused"
        raise GovernanceError(
            "apply refused: memory index artifact is required; "
            "tag-only storage governance is unsafe"
        )

        if config is None:
            report["status"] = "refused"
            raise GovernanceError("--apply requires --config so backup_max_count can be set to 1")
        if not config.is_file():
            raise GovernanceError(f"config does not exist: {config}")

        builder = index_builder or build_staging_tag_index
        expected_invariants = report["analysis"]["memory_invariants"]
        with _writer_lock(db) as lock_path:
            report["writer_lock"] = str(lock_path) if lock_path is not None else None
            with tempfile.TemporaryDirectory(
                prefix=".wave_memory_govern_",
                dir=db.parent,
            ) as temporary_dir:
                staging_dir = Path(temporary_dir)
                candidate_db = staging_dir / "wave_memory.candidate.db"
                create_candidate_database(db, candidate_db)
                report["candidate_changes"] = govern_candidate_database(candidate_db)
                report["acceptance"] = validate_candidate_database(
                    candidate_db,
                    expected_invariants,
                )
                artifact = builder(candidate_db, tag_index, dimension, staging_dir)
                _validate_index_artifact(artifact, candidate_db, dimension)

                target_generation_number = latest_generation(tag_index) + 1
                target_generation = generation_path(tag_index, target_generation_number)
                if target_generation.exists():
                    raise GovernanceError(
                        f"target tag index generation already exists: {target_generation}"
                    )
                target_generation.parent.mkdir(parents=True, exist_ok=True)
                os.replace(artifact.generation_path, target_generation)
                _fsync_file(target_generation)
                _fsync_directory(target_generation.parent)

                runtime_manifest = IndexManifest(
                    kind="tag",
                    generation=target_generation_number,
                    dimension=dimension,
                    db_watermark=artifact.manifest.db_watermark,
                    count=artifact.manifest.count,
                    checksum=artifact.manifest.checksum,
                    created_at=artifact.manifest.created_at,
                )
                manifest_destination = manifest_path(tag_index)
                original_manifest = (
                    manifest_destination.read_bytes()
                    if manifest_destination.exists()
                    else None
                )
                config_original: bytes | None = None
                config_updated = False
                original_moved = False
                candidate_installed = False
                manifest_switched = False
                backup_path = _unique_backup_path(db)

                try:
                    _checkpoint_runtime_db(db)
                    os.replace(db, backup_path)
                    original_moved = True
                    report["backup"] = str(backup_path)
                    _remove_sidecars(db)
                    os.replace(candidate_db, db)
                    candidate_installed = True
                    _fsync_file(db)
                    _fsync_directory(db.parent)

                    config_original = _update_config(config)
                    config_updated = True

                    _atomic_write_bytes(
                        manifest_destination,
                        _manifest_bytes(runtime_manifest),
                    )
                    manifest_switched = True
                    committed = read_index_manifest(
                        tag_index,
                        expected_kind="tag",
                        expected_dimension=dimension,
                    )
                    if committed != runtime_manifest:
                        raise GovernanceError("committed tag index manifest reload mismatch")
                except Exception:
                    if manifest_switched:
                        _restore_bytes(manifest_destination, original_manifest)
                    if config_updated and config_original is not None:
                        _atomic_write_bytes(config, config_original)
                    if original_moved:
                        if candidate_installed:
                            with contextlib.suppress(FileNotFoundError):
                                db.unlink()
                        if backup_path.exists():
                            os.replace(backup_path, db)
                            _fsync_file(db)
                            _fsync_directory(db.parent)
                        _remove_sidecars(db)
                    with contextlib.suppress(FileNotFoundError):
                        target_generation.unlink()
                    raise

                report["status"] = "applied"
                report["tag_index_manifest"] = runtime_manifest.to_dict()
                return report
    except LockUnavailableError as exc:
        report["status"] = "refused"
        report["error"] = str(exc)
        return report
    except Exception as exc:
        if report["status"] != "refused":
            report["status"] = "failed"
        report["error"] = str(exc)
        return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze or govern WaveMemory storage offline"
    )
    parser.add_argument("--db", required=True, help="Path to wave_memory.db")
    parser.add_argument(
        "--tag-index",
        default=None,
        help="Path to tags.hnsw (default: beside the database)",
    )
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--config", default=None, help="Path to the plugin JSON config")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Request apply (refused until dual memory/tag index staging is implemented)",
    )
    parser.add_argument(
        "--runtime-stopped-confirmed",
        action="store_true",
        help="Explicit confirmation that the WaveMemory runtime is stopped",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = govern_storage(
        args.db,
        tag_index_path=args.tag_index,
        dimension=args.dimension,
        config_path=args.config,
        apply=args.apply,
        runtime_stopped_confirmed=args.runtime_stopped_confirmed,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] in {"dry-run", "applied"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
