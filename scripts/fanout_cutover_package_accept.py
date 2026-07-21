#!/usr/bin/env python3
"""Accept a staged cutover package (vacuumed DB + rebuilt memory HNSW).

Readonly against production. Never switches live files.
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
    conn.row_factory = sqlite3.Row
    return conn


def accept(
    db: Path,
    index_base: Path,
    *,
    dimension: int = 1024,
    prod_db: Path | None = None,
) -> dict:
    import numpy as np

    from engine.vector_index import VectorIndex

    report: dict = {
        "generated_at": time.time(),
        "db": str(db),
        "index_base": str(index_base),
        "checks": {},
        "passed": False,
    }
    if not db.is_file():
        report["error"] = "db_missing"
        return report
    if not (Path(str(index_base) + ".manifest.json").is_file() or index_base.with_suffix(".hnsw.manifest.json").exists()):
        # VectorIndex expects base path like .../memory.hnsw with sibling .manifest.json
        pass

    conn = _ro(db)
    try:
        checks = report["checks"]
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        checks["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        checks["size_bytes"] = db.stat().st_size
        checks["memories"] = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        checks["marked"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0]
        checks["multi_families"] = (
            conn.execute(
                """SELECT COUNT(*) FROM (
                       SELECT 1 FROM scope_recovery_memory_map
                        GROUP BY legacy_memory_id HAVING COUNT(*) > 1
                   )"""
            ).fetchone()[0]
            if "scope_recovery_memory_map" in tables
            else 0
        )
        checks["formal"] = conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships"
        ).fetchone()[0]
        checks["main_formal"] = conn.execute(
            """SELECT COUNT(*) FROM scoped_soul_relationships
                WHERE bot_id='yushu' AND session_id='羽书:group:398291136'
                  AND visibility='group'"""
        ).fetchone()[0]
        checks["with_vector"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE vector IS NOT NULL AND length(vector)>0"
        ).fetchone()[0]
        checks["has_audit_table"] = (
            "scoped_soul_relationship_legacy_events" in tables
        )
        checks["audit_rows"] = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                ).fetchone()[0]
            )
            if checks["has_audit_table"]
            else 0
        )
        checks["audit_indexes"] = [
            str(r[0])
            for r in conn.execute(
                """SELECT name FROM sqlite_master
                    WHERE type='index'
                      AND tbl_name='scoped_soul_relationship_legacy_events'
                    ORDER BY name"""
            )
        ] if checks["has_audit_table"] else []
        checks["audit_subject_index"] = (
            "idx_legacy_rel_events_subject" in checks["audit_indexes"]
        )

        # Optional compare against live production audit/formal counts.
        if prod_db is not None and Path(prod_db).is_file():
            pc = _ro(Path(prod_db))
            try:
                pt = {
                    str(r[0])
                    for r in pc.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                prod_audit = (
                    int(
                        pc.execute(
                            "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                        ).fetchone()[0]
                    )
                    if "scoped_soul_relationship_legacy_events" in pt
                    else 0
                )
                prod_formal = int(
                    pc.execute(
                        "SELECT COUNT(*) FROM scoped_soul_relationships"
                    ).fetchone()[0]
                )
            finally:
                pc.close()
            checks["prod_audit_rows"] = prod_audit
            checks["prod_formal"] = prod_formal
            checks["audit_matches_prod"] = (
                checks["has_audit_table"] and checks["audit_rows"] == prod_audit
            )
            checks["formal_matches_prod"] = checks["formal"] == prod_formal
        else:
            checks["audit_matches_prod"] = checks["has_audit_table"]
            checks["formal_matches_prod"] = True

        # FTS
        try:
            fts_n = conn.execute(
                "SELECT COUNT(*) FROM fts_memories WHERE fts_memories MATCH '我是谁'"
            ).fetchone()[0]
            checks["fts_match_count"] = int(fts_n)
            checks["fts_ok"] = True
        except Exception as exc:
            checks["fts_ok"] = False
            checks["fts_error"] = str(exc)[:200]

        # Load HNSW and search using a real vector from DB
        index = VectorIndex(
            dimension=dimension,
            max_elements=max(int(checks["with_vector"]) + 1000, 5000),
            index_path=str(index_base),
            kind="memory",
            strict_manifest=True,
            allow_resize=True,
        )
        checks["index_count"] = int(index.index.get_current_count())
        checks["index_manifest"] = (
            index._manifest.to_dict() if getattr(index, "_manifest", None) is not None else None
        )

        sample = conn.execute(
            """SELECT id, vector, group_id, substr(content,1,80)
                 FROM memories
                WHERE vector IS NOT NULL AND length(vector)=?
                ORDER BY id DESC LIMIT 1""",
            (dimension * 4,),
        ).fetchone()
        if not sample:
            checks["vector_search"] = {"ok": False, "error": "no_sample_vector"}
        else:
            sid = int(sample[0])
            query = np.frombuffer(sample[1], dtype=np.float32)
            hits = index.search(query, k=5)
            hit_ids = [int(h[0]) for h in hits]
            # all hit ids must exist
            present = 0
            if hit_ids:
                ph = ",".join("?" for _ in hit_ids)
                present = conn.execute(
                    f"SELECT COUNT(*) FROM memories WHERE id IN ({ph})",
                    hit_ids,
                ).fetchone()[0]
            checks["vector_search"] = {
                "ok": bool(hits) and present == len(hit_ids) and sid in hit_ids,
                "query_id": sid,
                "query_group": sample[2],
                "hits": [{"id": int(i), "dist": float(d)} for i, d in hits],
                "hits_present_in_db": int(present),
                "self_in_hits": sid in hit_ids,
            }

        # affinity-ish formal sample
        top = conn.execute(
            """SELECT subject_principal_id, affinity, state
                 FROM scoped_soul_relationships
                WHERE bot_id='yushu' AND session_id='羽书:group:398291136'
                  AND visibility='group'
                ORDER BY affinity DESC LIMIT 3"""
        ).fetchall()
        checks["main_affinity_top"] = [
            {"subject": r[0], "affinity": r[1], "state": r[2]} for r in top
        ]

        report["passed"] = all(
            [
                checks.get("quick_check") == "ok",
                checks.get("marked") == 0,
                checks.get("multi_families") == 0,
                checks.get("formal", 0) >= 1000,
                checks.get("main_formal", 0) >= 300,
                checks.get("fts_ok") is True,
                checks.get("index_count", 0) > 0,
                (checks.get("vector_search") or {}).get("ok") is True,
                checks.get("has_audit_table") is True,
                checks.get("audit_rows", 0) > 0,
                checks.get("audit_subject_index") is True,
                checks.get("audit_matches_prod") is True,
                checks.get("formal_matches_prod") is True,
            ]
        )
        report["production_cutover_authorized"] = False
        report["hard_gates"] = {
            "vac_marked_zero": checks.get("marked") == 0,
            "audit_table_present": checks.get("has_audit_table") is True,
            "audit_rows_positive": checks.get("audit_rows", 0) > 0,
            "audit_subject_index": checks.get("audit_subject_index") is True,
            "audit_matches_prod": checks.get("audit_matches_prod") is True,
            "formal_matches_prod": checks.get("formal_matches_prod") is True,
        }
        report["verdict"] = (
            "cutover_package_accepted_off_prod"
            if report["passed"]
            else "cutover_package_failed"
        )
        return report
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
        ),
    )
    parser.add_argument(
        "--index-base",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/indexes/memory.hnsw"
        ),
    )
    parser.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
        help="production DB for audit/formal equality gates",
    )
    parser.add_argument("--dimension", type=int, default=1024)
    args = parser.parse_args()
    report = accept(
        Path(args.db),
        Path(args.index_base),
        dimension=int(args.dimension),
        prod_db=Path(args.prod_db) if args.prod_db else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
