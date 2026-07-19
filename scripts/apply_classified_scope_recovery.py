from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.db.outbox_repo import OutboxRepository
from engine.index_manifest import read_index_manifest, validate_index_manifest
from engine.vector_index import VectorIndex
from services.scope_recovery_migration import (
    CLASSIFIED_RECOVERY_RULE_VERSION,
    ScopeRecoveryMigrationError,
    apply_classified_scope_recovery,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _quick_check(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()


def _promote(staged: Path, production: Path, backup: Path, confirmation: str) -> dict[str, Any]:
    if confirmation != "promote-recovered-database":
        raise ScopeRecoveryMigrationError("promotion_confirmation_required")
    staged = staged.resolve()
    production = production.resolve()
    backup = backup.resolve()
    if not staged.is_file() or not production.is_file():
        raise ScopeRecoveryMigrationError("promotion_database_missing")
    if staged.parent.stat().st_dev != production.parent.stat().st_dev:
        raise ScopeRecoveryMigrationError("promotion_requires_same_filesystem")
    if _quick_check(staged) != "ok":
        raise ScopeRecoveryMigrationError("staged_database_integrity_failed")
    staged_conn = sqlite3.connect(staged)
    try:
        migration = staged_conn.execute(
            """SELECT run_id,status,indexes_status FROM scope_recovery_migrations
                 WHERE rule_version=? ORDER BY created_at DESC LIMIT 1""",
            (CLASSIFIED_RECOVERY_RULE_VERSION,),
        ).fetchone()
    finally:
        staged_conn.close()
    if migration is None or str(migration[1]) != "staged":
        raise ScopeRecoveryMigrationError("staged_recovery_marker_missing")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        raise ScopeRecoveryMigrationError("promotion_backup_already_exists")
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(production) + suffix)
        try:
            sidecar.unlink()
        except FileNotFoundError:
            pass
    os.replace(production, backup)
    try:
        os.replace(staged, production)
    except Exception:
        os.replace(backup, production)
        raise
    return {
        "run_id": str(migration[0]),
        "production_db": str(production),
        "promotion_backup": str(backup),
        "quick_check": _quick_check(production),
    }


def _valid_vectors(conn: sqlite3.Connection, query: str, dimension: int) -> tuple[list[int], list[bytes], int]:
    ids: list[int] = []
    vectors: list[bytes] = []
    invalid = 0
    expected_bytes = int(dimension) * 4
    for row_id, vector in conn.execute(query):
        raw = bytes(vector)
        if len(raw) != expected_bytes:
            invalid += 1
            continue
        ids.append(int(row_id))
        vectors.append(raw)
    return ids, vectors, invalid


def _save_index(
    *,
    ids: list[int],
    vectors: list[bytes],
    dimension: int,
    base_path: Path,
    kind: str,
    watermark: int,
) -> dict[str, Any]:
    index = VectorIndex(
        dimension=dimension,
        max_elements=max(len(ids) + 1, 1000),
        index_path=None,
        kind=kind,
    )
    for offset in range(0, len(ids), 5000):
        batch_ids = ids[offset:offset + 5000]
        batch_vectors = np.asarray(
            [np.frombuffer(raw, dtype=np.float32) for raw in vectors[offset:offset + 5000]],
            dtype=np.float32,
        )
        index.add(batch_ids, batch_vectors)
    index.index_path = str(base_path)
    manifest = index.save(db_watermark=int(watermark))
    if manifest is None:
        raise ScopeRecoveryMigrationError(f"{kind}_manifest_not_written")
    validate_index_manifest(
        manifest,
        base_path,
        expected_kind=kind,
        expected_dimension=dimension,
        verify_checksum=True,
    )
    return manifest.to_dict()


def _rebuild_indexes(database: Path, data_dir: Path, dimension: int, confirmation: str) -> dict[str, Any]:
    if confirmation != "rebuild-recovery-indexes":
        raise ScopeRecoveryMigrationError("index_rebuild_confirmation_required")
    database = database.resolve()
    data_dir = data_dir.resolve()
    conn = sqlite3.connect(database)
    try:
        memory_ids, memory_vectors, invalid_memory_vectors = _valid_vectors(
            conn,
            """SELECT id,vector FROM memories
                 WHERE vector IS NOT NULL AND resolution_state='resolved'
                   AND COALESCE(quarantine,0)=0
                   AND COALESCE(memory_type,'message') NOT IN ('archived','evicted','deleted')
                 ORDER BY id""",
            dimension,
        )
        tag_ids, tag_vectors, invalid_tag_vectors = _valid_vectors(
            conn,
            """SELECT id,embedding FROM tag_catalog
                 WHERE embedding IS NOT NULL AND status='active' ORDER BY id""",
            dimension,
        )
        watermark = OutboxRepository.committed_watermark(conn)
    finally:
        conn.close()
    memory_manifest = _save_index(
        ids=memory_ids,
        vectors=memory_vectors,
        dimension=dimension,
        base_path=data_dir / "memory.hnsw",
        kind="memory",
        watermark=watermark,
    )
    tag_manifest = _save_index(
        ids=tag_ids,
        vectors=tag_vectors,
        dimension=dimension,
        base_path=data_dir / "tags.hnsw",
        kind="tag",
        watermark=watermark,
    )
    conn = sqlite3.connect(database)
    try:
        conn.execute(
            """UPDATE scope_recovery_migrations
                  SET indexes_status='ready:memory_hnsw,tag_catalog_hnsw'
                WHERE run_id=(
                    SELECT run_id FROM scope_recovery_migrations
                     WHERE rule_version=? ORDER BY created_at DESC LIMIT 1
                )""",
            (CLASSIFIED_RECOVERY_RULE_VERSION,),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "memory": memory_manifest,
        "tag": tag_manifest,
        "invalid_memory_vectors": invalid_memory_vectors,
        "invalid_tag_vectors": invalid_tag_vectors,
    }


def _verify(database: Path, data_dir: Path, dimension: int) -> dict[str, Any]:
    database = database.resolve()
    data_dir = data_dir.resolve()
    conn = sqlite3.connect(database)
    try:
        run = conn.execute(
            """SELECT run_id,status,indexes_status,created_at,completed_at
                 FROM scope_recovery_migrations WHERE rule_version=?
                 ORDER BY created_at DESC LIMIT 1""",
            (CLASSIFIED_RECOVERY_RULE_VERSION,),
        ).fetchone()
        if run is None:
            raise ScopeRecoveryMigrationError("classified_recovery_run_missing")
        mapped = int(conn.execute(
            "SELECT COUNT(*) FROM scope_recovery_memory_map WHERE run_id=?",
            (run[0],),
        ).fetchone()[0])
        projected = int(conn.execute(
            """SELECT COUNT(*) FROM memories
                 WHERE provenance LIKE '%classified_legacy_recovery%' AND resolution_state='resolved'
                   AND COALESCE(quarantine,0)=0"""
        ).fetchone()[0])
        evicted_reactivated = int(conn.execute(
            """SELECT COUNT(*) FROM memories
                 WHERE provenance LIKE '%classified_legacy_recovery%'
                   AND memory_type='evicted'"""
        ).fetchone()[0])
        scoped_tag_links = int(conn.execute(
            """SELECT COUNT(*) FROM scope_recovery_items
                 WHERE run_id=? AND source_table='memory_tags' AND disposition='migrated'""",
            (run[0],),
        ).fetchone()[0])
        scoped_facts = int(conn.execute(
            """SELECT COUNT(*) FROM scope_recovery_items
                 WHERE run_id=? AND source_table='facts' AND disposition='migrated'""",
            (run[0],),
        ).fetchone()[0])
        memory_vector_count = int(conn.execute(
            """SELECT COUNT(*) FROM memories
                 WHERE vector IS NOT NULL AND resolution_state='resolved'
                   AND COALESCE(quarantine,0)=0
                   AND COALESCE(memory_type,'message') NOT IN ('archived','evicted','deleted')"""
        ).fetchone()[0])
        tag_vector_count = int(conn.execute(
            "SELECT COUNT(*) FROM tag_catalog WHERE embedding IS NOT NULL AND status='active'"
        ).fetchone()[0])
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()
    memory_manifest = read_index_manifest(
        data_dir / "memory.hnsw",
        expected_kind="memory",
        expected_dimension=dimension,
        verify_checksum=True,
    )
    tag_manifest = read_index_manifest(
        data_dir / "tags.hnsw",
        expected_kind="tag",
        expected_dimension=dimension,
        verify_checksum=True,
    )
    return {
        "run": {
            "run_id": run[0],
            "status": run[1],
            "indexes_status": run[2],
            "created_at": run[3],
            "completed_at": run[4],
        },
        "database": {
            "quick_check": quick_check,
            "mapped_memories": mapped,
            "projected_memories": projected,
            "evicted_reactivated": evicted_reactivated,
            "scoped_tag_links": scoped_tag_links,
            "scoped_facts": scoped_facts,
            "memory_vector_count": memory_vector_count,
            "tag_vector_count": tag_vector_count,
        },
        "memory_manifest": None if memory_manifest is None else memory_manifest.to_dict(),
        "tag_manifest": None if tag_manifest is None else tag_manifest.to_dict(),
        "memory_manifest_matches_db": memory_manifest is not None and memory_manifest.count == memory_vector_count,
        "tag_manifest_matches_db": tag_manifest is not None and tag_manifest.count == tag_vector_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply and verify classified formal Scope recovery")
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage")
    stage.add_argument("--source-db", required=True)
    stage.add_argument("--classification-report", required=True)
    stage.add_argument("--output-db", required=True)
    stage.add_argument("--run-dir", required=True)
    stage.add_argument("--confirmation", choices=("recover",), required=True)

    promote = subparsers.add_parser("promote")
    promote.add_argument("--staged-db", required=True)
    promote.add_argument("--production-db", required=True)
    promote.add_argument("--backup-db", required=True)
    promote.add_argument("--confirmation", choices=("promote-recovered-database",), required=True)

    rebuild = subparsers.add_parser("rebuild-indexes")
    rebuild.add_argument("--database", required=True)
    rebuild.add_argument("--data-dir", required=True)
    rebuild.add_argument("--dimension", type=int, required=True)
    rebuild.add_argument("--confirmation", choices=("rebuild-recovery-indexes",), required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--database", required=True)
    verify.add_argument("--data-dir", required=True)
    verify.add_argument("--dimension", type=int, required=True)

    args = parser.parse_args()
    if args.command == "stage":
        result = apply_classified_scope_recovery(
            args.source_db,
            args.classification_report,
            args.output_db,
            args.run_dir,
            confirmation=args.confirmation,
        )
    elif args.command == "promote":
        result = _promote(Path(args.staged_db), Path(args.production_db), Path(args.backup_db), args.confirmation)
    elif args.command == "rebuild-indexes":
        result = _rebuild_indexes(Path(args.database), Path(args.data_dir), args.dimension, args.confirmation)
    else:
        result = _verify(Path(args.database), Path(args.data_dir), args.dimension)
    print(_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
