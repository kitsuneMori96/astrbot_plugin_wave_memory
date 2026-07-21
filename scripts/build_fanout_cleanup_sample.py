#!/usr/bin/env python3
"""Build a non-production sample DB from production for fanout cleanup E2E."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prod-db", required=True)
    parser.add_argument("--out-db", required=True)
    parser.add_argument("--families", type=int, default=50)
    args = parser.parse_args()

    prod = Path(args.prod_db)
    sample = Path(args.out_db)
    sample.parent.mkdir(parents=True, exist_ok=True)
    if sample.exists():
        sample.unlink()

    src = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=120)
    src.execute("PRAGMA query_only=ON")
    dst = sqlite3.connect(sample.as_posix())
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("PRAGMA synchronous=OFF")

    tables = (
        "memories",
        "scope_recovery_memory_map",
        "scoped_memory_tags",
        "scoped_memory_effective_tags",
        "tag_extraction_status",
        "scoped_facts",
        "scoped_fact_history",
        "scoped_beliefs",
    )
    for table in tables:
        row = src.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row and row[0]:
            dst.execute(row[0])

    families = [
        r[0]
        for r in src.execute(
            """SELECT legacy_memory_id FROM scope_recovery_memory_map
               GROUP BY legacy_memory_id HAVING COUNT(*) > 1
               ORDER BY legacy_memory_id LIMIT ?""",
            (int(args.families),),
        ).fetchall()
    ]
    if not families:
        raise SystemExit("no multi-target families found")

    ph = ",".join("?" for _ in families)
    map_cols = [r[1] for r in src.execute("PRAGMA table_info(scope_recovery_memory_map)")]
    map_rows = src.execute(
        f"SELECT {','.join(map_cols)} FROM scope_recovery_memory_map WHERE legacy_memory_id IN ({ph})",
        families,
    ).fetchall()
    dst.executemany(
        f"INSERT INTO scope_recovery_memory_map({','.join(map_cols)}) VALUES ({','.join('?' for _ in map_cols)})",
        map_rows,
    )

    tid_i = map_cols.index("target_memory_id")
    lid_i = map_cols.index("legacy_memory_id")
    target_ids = sorted({int(r[tid_i]) for r in map_rows})
    legacy_ids = sorted({int(r[lid_i]) for r in map_rows})
    mem_ids = sorted(set(target_ids) | set(legacy_ids))

    mem_cols = [r[1] for r in src.execute("PRAGMA table_info(memories)")]
    batch = 200
    for i in range(0, len(mem_ids), batch):
        chunk = mem_ids[i : i + batch]
        phc = ",".join("?" for _ in chunk)
        rows = src.execute(
            f"SELECT {','.join(mem_cols)} FROM memories WHERE id IN ({phc})",
            chunk,
        ).fetchall()
        dst.executemany(
            f"INSERT INTO memories({','.join(mem_cols)}) VALUES ({','.join('?' for _ in mem_cols)})",
            rows,
        )

    def copy_ref(table: str, col: str) -> int:
        names = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table not in names:
            return 0
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        if col not in cols:
            return 0
        total = 0
        for i in range(0, len(mem_ids), batch):
            chunk = mem_ids[i : i + batch]
            phc = ",".join("?" for _ in chunk)
            rows = src.execute(
                f"SELECT {','.join(cols)} FROM {table} WHERE {col} IN ({phc})",
                chunk,
            ).fetchall()
            if not rows:
                continue
            dst.executemany(
                f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                rows,
            )
            total += len(rows)
        return total

    ref_counts = {
        "scoped_memory_tags": copy_ref("scoped_memory_tags", "memory_id"),
        "scoped_memory_effective_tags": copy_ref("scoped_memory_effective_tags", "memory_id"),
        "tag_extraction_status": copy_ref("tag_extraction_status", "memory_id"),
        "scoped_facts": copy_ref("scoped_facts", "source_memory_id"),
        "scoped_fact_history": copy_ref("scoped_fact_history", "source_memory_id"),
        "scoped_beliefs": copy_ref("scoped_beliefs", "source_memory_id"),
    }

    dst.commit()
    src.close()
    dst.close()

    meta = {
        "created_at": time.time(),
        "families": len(families),
        "mem_ids": len(mem_ids),
        "target_ids": len(target_ids),
        "legacy_ids": len(legacy_ids),
        "ref_counts": ref_counts,
        "sample_path": str(sample),
        "size_bytes": sample.stat().st_size,
    }
    meta_path = sample.with_suffix(sample.suffix + ".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
