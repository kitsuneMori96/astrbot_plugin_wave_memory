#!/usr/bin/env python3
"""Cutover preflight for fanout physical cleanup (never switches production).

Checks staged cleaned DB health, free space, vector coverage, and optional:
  --vacuum-into PATH     write a compacted copy via VACUUM INTO
  --rebuild-memory-hnsw DIR  rebuild memory.hnsw* under DIR from staged vectors
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def inspect_db(path: Path) -> dict:
    conn = _ro(path)
    try:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        freelist = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        memories = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        marked = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
        with_vector = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE vector IS NOT NULL AND length(vector)>0"
            ).fetchone()[0]
        )
        formal = int(conn.execute("SELECT COUNT(*) FROM scoped_soul_relationships").fetchone()[0])
        multi = int(
            conn.execute(
                """SELECT COUNT(*) FROM (
                       SELECT 1 FROM scope_recovery_memory_map
                        GROUP BY legacy_memory_id HAVING COUNT(*)>1
                   )"""
            ).fetchone()[0]
        )
        fts_triggers = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'fts_memories_%'"
            ).fetchall()
        ]
        fts_ok = False
        fts_error = None
        try:
            conn.execute(
                "SELECT rowid FROM fts_memories WHERE fts_memories MATCH '我是谁' LIMIT 1"
            ).fetchone()
            fts_ok = True
        except Exception as exc:  # pragma: no cover
            fts_error = str(exc)[:200]
        dim = None
        sample = conn.execute(
            "SELECT length(vector) FROM memories WHERE vector IS NOT NULL LIMIT 1"
        ).fetchone()
        if sample and sample[0]:
            dim = int(sample[0]) // 4
        return {
            "path": str(path),
            "exists": True,
            "size_bytes": path.stat().st_size,
            "quick_check": conn.execute("PRAGMA quick_check").fetchone()[0],
            "page_count": page_count,
            "page_size": page_size,
            "freelist_count": freelist,
            "reclaimable_bytes_est": freelist * page_size,
            "memories": memories,
            "marked": marked,
            "with_vector": with_vector,
            "without_vector": memories - with_vector,
            "vector_dim_guess": dim,
            "formal_relationships": formal,
            "multi_target_families": multi,
            "fts_triggers": fts_triggers,
            "fts_query_ok": fts_ok,
            "fts_error": fts_error,
        }
    finally:
        conn.close()


def vacuum_into(source: Path, dest: Path) -> dict:
    if dest.exists():
        raise SystemExit(f"vacuum destination exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    conn = sqlite3.connect(source.as_posix(), timeout=600)
    try:
        # VACUUM INTO creates a compacted independent file; does not modify source schema locks long.
        conn.execute(f"VACUUM INTO '{dest.as_posix()}'")
    finally:
        conn.close()
    elapsed = round(time.time() - t0, 1)
    before = source.stat().st_size
    after = dest.stat().st_size
    return {
        "source": str(source),
        "dest": str(dest),
        "seconds": elapsed,
        "source_bytes": before,
        "dest_bytes": after,
        "saved_bytes": before - after,
        "dest_inspect": inspect_db(dest),
    }


def rebuild_memory_hnsw(database: Path, data_dir: Path, dimension: int) -> dict:
    import numpy as np

    from engine.vector_index import VectorIndex

    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=600)
    try:
        rows = conn.execute(
            """SELECT id, vector FROM memories
                WHERE vector IS NOT NULL AND length(vector)>0
                ORDER BY id"""
        ).fetchall()
    finally:
        conn.close()

    ids: list[int] = []
    vectors: list = []
    invalid = 0
    for mid, raw in rows:
        try:
            arr = np.frombuffer(raw, dtype=np.float32)
            if arr.shape[0] != dimension:
                invalid += 1
                continue
            ids.append(int(mid))
            vectors.append(arr)
        except Exception:
            invalid += 1

    t0 = time.time()
    index = VectorIndex(
        dimension=dimension,
        max_elements=max(len(ids) + 1, 1000),
        index_path=None,
        kind="memory",
    )
    batch = 5000
    for offset in range(0, len(ids), batch):
        batch_ids = ids[offset : offset + batch]
        batch_vectors = np.asarray(vectors[offset : offset + batch], dtype=np.float32)
        index.add(batch_ids, batch_vectors)
    base = data_dir / "memory.hnsw"
    index.index_path = str(base)
    # watermark not critical for staged probe
    manifest = index.save(db_watermark=0)
    elapsed = round(time.time() - t0, 1)
    return {
        "data_dir": str(data_dir),
        "base_path": str(base),
        "count": len(ids),
        "invalid_vectors": invalid,
        "seconds": elapsed,
        "manifest": manifest.to_dict() if manifest is not None and hasattr(manifest, "to_dict") else str(manifest),
        "files": sorted(p.name for p in data_dir.glob("memory.hnsw*")),
    }


def disk_stats(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    return {
        "path": str(path),
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "free_gb": round(usage.free / (1024**3), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged-db", required=True)
    parser.add_argument("--prod-db", default="")
    parser.add_argument("--vacuum-into", default="")
    parser.add_argument("--rebuild-memory-hnsw-dir", default="")
    parser.add_argument("--dimension", type=int, default=1024)
    args = parser.parse_args()

    staged = Path(args.staged_db)
    report: dict = {
        "generated_at": time.time(),
        "disk": disk_stats(staged.parent if staged.exists() else Path(".")),
        "staged": inspect_db(staged) if staged.is_file() else {"exists": False, "path": str(staged)},
        "production_cutover_authorized": False,
        "phase2_promote_allowed": False,
    }
    if args.prod_db:
        prod = Path(args.prod_db)
        report["prod"] = inspect_db(prod) if prod.is_file() else {"exists": False}

    staged_ok = (
        report["staged"].get("exists")
        and report["staged"].get("quick_check") == "ok"
        and report["staged"].get("marked") == 0
        and report["staged"].get("multi_target_families") == 0
        and report["staged"].get("fts_query_ok") is True
    )
    report["staged_ready_for_cutover_tech"] = bool(staged_ok)
    report["blockers_for_live_cutover"] = []
    if not staged_ok:
        report["blockers_for_live_cutover"].append("staged_not_ready")
    report["blockers_for_live_cutover"].append("missing_user_authorization")
    report["blockers_for_live_cutover"].append("vector_index_cutover_not_applied_to_prod_data_dir")

    if args.vacuum_into:
        report["vacuum"] = vacuum_into(staged, Path(args.vacuum_into))
    if args.rebuild_memory_hnsw_dir:
        report["memory_hnsw_rebuild"] = rebuild_memory_hnsw(
            staged,
            Path(args.rebuild_memory_hnsw_dir),
            int(args.dimension),
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
