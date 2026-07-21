#!/usr/bin/env python3
"""Dry-run cutover runbook for fanout cleanup package.

Default mode never mutates production. It prints///writes a machine-checkable
plan: refresh recommendation, asset paths, drift, and ordered cutover steps.

Live apply is intentionally NOT implemented here — production switch requires
explicit human ops after authorization.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _stats(path: Path) -> dict:
    conn = _ro(path)
    try:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        audit = 0
        audit_indexes: list[str] = []
        if "scoped_soul_relationship_legacy_events" in tables:
            audit = int(
                conn.execute(
                    "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                ).fetchone()[0]
            )
            audit_indexes = [
                str(r[0])
                for r in conn.execute(
                    """SELECT name FROM sqlite_master
                        WHERE type='index'
                          AND tbl_name='scoped_soul_relationship_legacy_events'
                        ORDER BY name"""
                )
            ]
        formal_row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships"
        ).fetchone()
        return {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "mtime": path.stat().st_mtime if path.is_file() else None,
            "memories": conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
            "marked": conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0],
            "max_id": conn.execute("SELECT MAX(id) FROM memories").fetchone()[0],
            "max_ts": conn.execute("SELECT MAX(timestamp) FROM memories").fetchone()[0],
            "formal": int(formal_row[0] or 0),
            "formal_affinity_sum": int(formal_row[1] or 0),
            "audit_rows": audit,
            "has_audit_table": "scoped_soul_relationship_legacy_events" in tables,
            "audit_indexes": audit_indexes,
            "multi_families": conn.execute(
                """SELECT COUNT(*) FROM (
                       SELECT 1 FROM scope_recovery_memory_map
                        GROUP BY legacy_memory_id HAVING COUNT(*) > 1
                   )"""
            ).fetchone()[0]
            if "scope_recovery_memory_map" in tables
            else 0,
        }
    finally:
        conn.close()


def build_plan(
    *,
    prod: Path,
    vacuumed: Path,
    index_dir: Path,
    accept_refresh_if_drift: bool = True,
) -> dict:
    prod_s = _stats(prod)
    vac_s = _stats(vacuumed)
    conn = _ro(prod)
    try:
        newer_ts = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE timestamp > ?",
            (vac_s.get("max_ts") or 0,),
        ).fetchone()[0]
        newer_non_fanout = conn.execute(
            """SELECT COUNT(*) FROM memories
                WHERE timestamp > ?
                  AND (provenance NOT LIKE '%fanout_duplicate%' OR provenance IS NULL)""",
            (vac_s.get("max_ts") or 0,),
        ).fetchone()[0]
        newer_ids = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE id > ?",
            (vac_s.get("max_id") or 0,),
        ).fetchone()[0]
    finally:
        conn.close()

    drift = {
        "prod_minus_vac_memories": int(prod_s["memories"] - vac_s["memories"]),
        "formal_delta": int(prod_s["formal"] - vac_s["formal"]),
        "formal_affinity_sum_delta": int(
            prod_s["formal_affinity_sum"] - vac_s["formal_affinity_sum"]
        ),
        "audit_delta": int(prod_s["audit_rows"] - vac_s["audit_rows"]),
        "audit_preserved": bool(
            vac_s.get("has_audit_table")
            and prod_s.get("audit_rows", 0) == vac_s.get("audit_rows", 0)
        ),
        "prod_rows_timestamp_newer_than_vac_max_ts": int(newer_ts),
        "prod_non_fanout_timestamp_newer_than_vac_max_ts": int(newer_non_fanout),
        "prod_ids_gt_vac_max_id": int(newer_ids),
        "marked_still_in_prod": int(prod_s["marked"]),
        "vac_marked": int(vac_s["marked"]),
    }
    hard_gates = {
        "vac_marked_zero": int(vac_s["marked"] or 0) == 0,
        "audit_table_present_in_package": bool(vac_s.get("has_audit_table")),
        "audit_count_matches_prod": drift["audit_preserved"],
        "audit_subject_index_present": "idx_legacy_rel_events_subject"
        in (vac_s.get("audit_indexes") or []),
        "formal_count_matches": drift["formal_delta"] == 0,
        "no_non_fanout_memory_drift": drift[
            "prod_non_fanout_timestamp_newer_than_vac_max_ts"
        ]
        == 0
        and drift["prod_ids_gt_vac_max_id"] == 0,
    }
    needs_refresh = (
        drift["prod_non_fanout_timestamp_newer_than_vac_max_ts"] > 0
        or drift["formal_delta"] != 0
        or drift["prod_ids_gt_vac_max_id"] > 0
        or not drift["audit_preserved"]
        or not hard_gates["vac_marked_zero"]
        or not hard_gates["audit_subject_index_present"]
    )
    package_safe_for_cutover = all(hard_gates.values()) and not needs_refresh

    steps = []
    if needs_refresh and accept_refresh_if_drift:
        steps.extend(
            [
                "MAINT: pause or drain writers if possible",
                "REFRESH: sqlite backup prod -> new staged full copy (must include scoped_soul_relationship_legacy_events)",
                "CLEAN: fanout_physical_cleanup.py --apply on staged copy (confirmation token)",
                "VERIFY-AUDIT: vac_audit_count == prod_audit_count after clean",
                "VACUUM: VACUUM INTO compact staged file",
                "INDEX: rebuild memory.hnsw into staged indexes dir from cleaned DB",
                "ACCEPT: fanout_cutover_package_accept.py on new package (includes audit gates)",
            ]
        )
    else:
        steps.append("PACKAGE: reuse current vacuumed DB + indexes (all hard gates pass)")

    steps.extend(
        [
            "AUTH: require explicit user cutover authorization (not event-audit-only auth)",
            "BACKUP: rename/copy current prod wave_memory.db and memory.hnsw* as rollback",
            "SWAP-DB: atomically replace prod wave_memory.db with vacuumed cleaned DB",
            "SWAP-INDEX: install staged memory.hnsw* into plugin data_dir",
            "RESTART: reload plugin / process so handles reopen files",
            "VERIFY: person_search/affinity smoke + audit summary + fanout_risk_monitor + FTS",
            "ROLLBACK-IF-NEEDED: restore backup DB+index files and restart",
        ]
    )

    return {
        "mode": "dry-run",
        "generated_at": time.time(),
        "production_apply_implemented": False,
        "phase2_promote_allowed": False,
        "assets": {
            "prod_db": str(prod),
            "vacuumed_db": str(vacuumed),
            "index_dir": str(index_dir),
            "index_files": sorted(p.name for p in index_dir.glob("memory.hnsw*"))
            if index_dir.is_dir()
            else [],
        },
        "prod": prod_s,
        "vacuumed": vac_s,
        "drift": drift,
        "hard_gates": hard_gates,
        "package_safe_for_cutover": package_safe_for_cutover,
        "needs_refresh_before_cutover": needs_refresh,
        "direct_swap_risk": (
            "loses_prod_writes_or_audit_if_stale"
            if needs_refresh
            else "low_for_current_gates"
        ),
        "ordered_steps": steps,
        "authorization_required": [
            "user_explicit_cutover_authorization",
            "maintenance_window",
            "rollback_file_retention",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument(
        "--vacuumed-db",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
        ),
    )
    parser.add_argument(
        "--index-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/indexes"
        ),
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    plan = build_plan(
        prod=Path(args.prod_db),
        vacuumed=Path(args.vacuumed_db),
        index_dir=Path(args.index_dir),
    )
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
