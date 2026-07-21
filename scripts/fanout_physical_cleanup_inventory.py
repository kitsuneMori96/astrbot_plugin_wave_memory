#!/usr/bin/env python3
"""Readonly inventory for staged physical cleanup of fanout_duplicate rows.

Never deletes. Classifies multi-target recovery families and estimates the
safe delete set under the default keeper rule:

  keep unmarked legacy_memory_id; delete all marked map targets.

This is intentionally separate from classified promote / Phase-2 recovery.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


REF_MEMORY_COLUMNS = (
    "memory_id",
    "source_memory_id",
    "target_memory_id",
    "related_memory_id",
)


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _ref_tables(conn: sqlite3.Connection) -> list[dict]:
    out: list[dict] = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
        hits = [col for col in cols if col in REF_MEMORY_COLUMNS]
        if hits:
            out.append({"table": name, "columns": hits})
    return out


def inventory(conn: sqlite3.Connection) -> dict:
    """Prefer cheap aggregates; sample families for keeper-pattern proof."""
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    marked = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
    ).fetchone()[0]
    families = conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT 1 FROM scope_recovery_memory_map
                GROUP BY legacy_memory_id HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]
    map_targets = conn.execute("SELECT COUNT(*) FROM scope_recovery_memory_map").fetchone()[0]
    delete_if_keep_one = conn.execute(
        """SELECT SUM(n - 1) FROM (
               SELECT COUNT(*) AS n FROM scope_recovery_memory_map
                GROUP BY legacy_memory_id HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]
    map_targets_unmarked = conn.execute(
        """SELECT COUNT(*)
             FROM scope_recovery_memory_map map
             JOIN memories m ON m.id = map.target_memory_id
            WHERE m.provenance NOT LIKE '%fanout_duplicate%'
               OR m.provenance IS NULL"""
    ).fetchone()[0]

    # Sample multi-target families for keeper pattern (full join is too heavy online).
    sample = conn.execute(
        """
        SELECT map.legacy_memory_id,
               COUNT(*) AS targets,
               SUM(CASE WHEN m.provenance LIKE '%fanout_duplicate%' THEN 1 ELSE 0 END) AS marked_targets,
               MAX(CASE WHEN leg.id IS NULL THEN 0 ELSE 1 END) AS legacy_exists,
               MAX(CASE WHEN leg.provenance LIKE '%fanout_duplicate%' THEN 1 ELSE 0 END) AS legacy_marked
          FROM scope_recovery_memory_map map
          LEFT JOIN memories m ON m.id = map.target_memory_id
          LEFT JOIN memories leg ON leg.id = map.legacy_memory_id
         WHERE map.legacy_memory_id IN (
               SELECT legacy_memory_id FROM scope_recovery_memory_map
                GROUP BY legacy_memory_id HAVING COUNT(*) > 1
                LIMIT 200
         )
         GROUP BY map.legacy_memory_id
        """
    ).fetchall()
    keep_legacy = sum(
        1
        for _lid, targets, marked_targets, legacy_exists, legacy_marked in sample
        if targets == marked_targets and legacy_exists == 1 and legacy_marked == 0
    )
    need_keep_one = sum(
        1
        for _lid, targets, marked_targets, legacy_exists, legacy_marked in sample
        if targets == marked_targets and (legacy_exists == 0 or legacy_marked == 1)
    )
    partial = sum(
        1
        for _lid, targets, marked_targets, _le, _lm in sample
        if marked_targets < targets
    )
    sample_uniform = keep_legacy == len(sample) and need_keep_one == 0 and partial == 0

    # If sample is uniform and marked count equals multi-family targets, eligible.
    multi_family_targets = conn.execute(
        """SELECT SUM(n) FROM (
               SELECT COUNT(*) AS n FROM scope_recovery_memory_map
                GROUP BY legacy_memory_id HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]
    safe_delete_all_marked_targets = bool(
        sample_uniform and int(multi_family_targets or 0) == int(marked or 0)
    )

    top_groups = conn.execute(
        """SELECT group_id, COUNT(*) AS n
             FROM memories
            WHERE provenance LIKE '%fanout_duplicate%'
            GROUP BY group_id
            ORDER BY n DESC
            LIMIT 12"""
    ).fetchall()

    return {
        "generated_at": time.time(),
        "counts": {
            "total_memories": total,
            "fanout_marked": marked,
            "marked_ratio": round(marked / total, 4) if total else None,
            "multi_target_families": families,
            "map_targets": map_targets,
            "multi_family_targets": int(multi_family_targets or 0),
            "delete_if_keep_one_target": int(delete_if_keep_one or 0),
            "map_targets_unmarked": map_targets_unmarked,
        },
        "family_patterns": {
            "sample_size": len(sample),
            "sample_keep_unmarked_legacy_delete_all_targets": keep_legacy,
            "sample_all_marked_need_keep_one_target": need_keep_one,
            "sample_partial_marked": partial,
            "sample_uniform_keep_legacy": sample_uniform,
            "safe_delete_row_estimate": int(marked or 0) if safe_delete_all_marked_targets else None,
            "families": families,
        },
        "top_marked_groups": [
            {"group_id": g, "marked_rows": n} for g, n in top_groups
        ],
        "referencing_tables": _ref_tables(conn),
        "recommended_keeper_rule": {
            "name": "keep_unmarked_legacy_delete_all_marked_map_targets",
            "eligible": safe_delete_all_marked_targets,
            "delete_rows": int(marked or 0) if safe_delete_all_marked_targets else None,
            "notes": [
                "Do not re-open classified fanout promote",
                "Only staged-copy first; never delete production in-place without backup",
                "Cascade-clean referencing tables for deleted memory ids",
                "Rebuild/reindex vectors after delete; VACUUM only after verification",
            ],
        },
        "verdict": {
            "physical_cleanup_ready_for_staging": bool(safe_delete_all_marked_targets),
            "production_delete_authorized": False,
            "phase2_promote_allowed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    db = Path(args.db)
    if not db.is_file():
        raise SystemExit(f"database missing: {db}")
    conn = _connect(db)
    try:
        print(json.dumps(inventory(conn), ensure_ascii=False, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
