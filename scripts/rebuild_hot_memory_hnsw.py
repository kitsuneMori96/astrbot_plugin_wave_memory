#!/usr/bin/env python3
"""Rebuild bounded hot memory.hnsw from active candidates only.

Does NOT delete memories rows, does NOT fanout/promote.
After soft-delete, HNSW still holds deleted/evicted labels and wastes knn slots;
SQL post-filter drops them, but rank pollution hurts recall quality.

Usage (dry-run default):
  python scripts/rebuild_hot_memory_hnsw.py --db ... --index-dir ...

Apply:
  python scripts/rebuild_hot_memory_hnsw.py --db ... --index-dir ... \\
    --apply --confirmation rebuild-hot-memory-hnsw --allow-production
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

CONFIRMATION = "rebuild-hot-memory-hnsw"


def _ensure_path() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _is_prod_like_db(path: Path) -> bool:
    p = path.as_posix()
    return path.name == "wave_memory.db" and "plugin_data" in p and "backups" not in p


def _watermark(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM memories").fetchone()[0])
    except Exception:
        return 0


def inventory(conn: sqlite3.Connection, *, dimension: int, max_vectors: int) -> dict[str, Any]:
    _ensure_path()
    from services.memory_index_policy import (
        MemoryIndexPolicy,
        select_hot_memory_candidates,
    )

    policy = MemoryIndexPolicy(max_vectors=int(max_vectors))
    candidates = select_hot_memory_candidates(conn, policy, int(dimension))
    with_vec = sum(1 for c in candidates if c.vector is not None)
    return {
        "dimension": int(dimension),
        "max_vectors": int(max_vectors),
        "candidate_count": len(candidates),
        "with_vector": with_vec,
        "watermark": _watermark(conn),
        "sample_ids": [int(c.memory_id) for c in candidates[:10]],
    }


def rebuild(
    conn: sqlite3.Connection,
    *,
    index_dir: Path,
    dimension: int,
    max_vectors: int,
    retention: int = 1,
) -> dict[str, Any]:
    _ensure_path()
    from engine.vector_index import VectorIndex
    from services.memory_index_policy import (
        MemoryIndexPolicy,
        select_hot_memory_candidates,
    )

    policy = MemoryIndexPolicy(max_vectors=int(max_vectors))
    candidates = select_hot_memory_candidates(conn, policy, int(dimension))
    index_path = index_dir / "memory.hnsw"
    # Build empty in-memory index; only point path when saving a new generation.
    index = VectorIndex(
        dimension=int(dimension),
        max_elements=int(max_vectors),
        index_path=None,
        kind="memory",
        allow_resize=False,
        generation_retention=int(retention),
        strict_manifest=False,
    )
    index.index_path = str(index_path)

    ids: list[int] = []
    for offset in range(0, len(candidates), 2000):
        batch = candidates[offset : offset + 2000]
        batch_ids = [int(c.memory_id) for c in batch if c.vector is not None]
        vectors = [c.vector for c in batch if c.vector is not None]
        if not batch_ids:
            continue
        index.add(batch_ids, np.asarray(vectors, dtype=np.float32))
        ids.extend(batch_ids)

    watermark = _watermark(conn)
    manifest = index.save(db_watermark=watermark)
    return {
        "index_path": str(index_path),
        "added": len(ids),
        "candidate_count": len(candidates),
        "watermark": watermark,
        "manifest": manifest.to_dict() if manifest is not None else None,
    }


def verify(conn: sqlite3.Connection, *, index_dir: Path, dimension: int) -> dict[str, Any]:
    _ensure_path()
    from engine.vector_index import VectorIndex

    index = VectorIndex(
        dimension=int(dimension),
        max_elements=100_000,
        index_path=str(index_dir / "memory.hnsw"),
        kind="memory",
        allow_resize=False,
        strict_manifest=True,
    )
    get_ids = getattr(index.index, "get_ids_list", None)
    ids = list(get_ids()) if callable(get_ids) else []
    inactive = 0
    for i in range(0, len(ids), 2000):
        chunk = [int(x) for x in ids[i : i + 2000]]
        if not chunk:
            continue
        ph = ",".join("?" * len(chunk))
        inactive += int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM memories
                 WHERE id IN ({ph})
                   AND (
                        COALESCE(quarantine, 0) != 0
                     OR COALESCE(memory_type, 'message') IN
                        ('archived', 'evicted', 'deleted', 'noise')
                   )
                """,
                chunk,
            ).fetchone()[0]
        )
    return {
        "index_count": len(ids),
        "inactive_in_index": inactive,
        "active_in_index": len(ids) - inactive,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--dimension", type=int, default=1024)
    parser.add_argument("--max-vectors", type=int, default=100000)
    parser.add_argument(
        "--generation-retention",
        type=int,
        default=1,
        help="HNSW generations to keep after publishing (default 1, matching runtime).",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", type=str, default="")
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()

    db: Path = args.db
    index_dir: Path = args.index_dir
    if not db.is_file():
        print(json.dumps({"ok": False, "error": "db_missing"}, ensure_ascii=False))
        return 1
    if not index_dir.is_dir():
        print(json.dumps({"ok": False, "error": "index_dir_missing"}, ensure_ascii=False))
        return 1

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=300)
    conn.execute("PRAGMA query_only=ON")
    try:
        inv = inventory(conn, dimension=args.dimension, max_vectors=args.max_vectors)
    finally:
        conn.close()

    if not args.apply:
        out = {
            "ok": True,
            "mode": "dry-run",
            "generated_at": time.time(),
            "db": str(db),
            "index_dir": str(index_dir),
            "confirmation_for_apply": CONFIRMATION,
            "fanout_allowed": False,
            "memories_rows_unchanged": True,
            **inv,
        }
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(json.dumps({"ok": True, "wrote": str(args.out), "candidates": inv["candidate_count"]}, ensure_ascii=False))
        else:
            print(text)
        return 0

    if args.confirmation != CONFIRMATION:
        print(json.dumps({"ok": False, "error": "confirmation_mismatch", "expected": CONFIRMATION}, ensure_ascii=False, indent=2))
        return 2
    if _is_prod_like_db(db) and not args.allow_production:
        print(json.dumps({"ok": False, "error": "allow_production_required"}, ensure_ascii=False, indent=2))
        return 2

    # Rebuild needs read of vectors; use RW only for safety on busy DB path (no SQL writes).
    conn = sqlite3.connect(db.as_posix(), timeout=600)
    conn.execute("PRAGMA query_only=ON")
    try:
        result = rebuild(
            conn,
            index_dir=index_dir,
            dimension=args.dimension,
            max_vectors=args.max_vectors,
            retention=max(1, int(args.generation_retention)),
        )
        verify_result = verify(conn, index_dir=index_dir, dimension=args.dimension)
    finally:
        conn.close()

    out = {
        "ok": True,
        "mode": "apply_rebuild_hot_memory_hnsw",
        "generated_at": time.time(),
        "db": str(db),
        "index_dir": str(index_dir),
        "fanout_allowed": False,
        "memories_rows_unchanged": True,
        "inventory": inv,
        "rebuild": result,
        "verify": verify_result,
        "note": "Runtime process must reload/restart to pick up new memory.hnsw generation.",
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": True,
                    "wrote": str(args.out),
                    "added": result["added"],
                    "inactive_in_index": verify_result["inactive_in_index"],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
