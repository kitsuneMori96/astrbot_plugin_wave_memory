#!/usr/bin/env python3
"""Refresh staged fanout cutover package from current production.

Pipeline (never switches production):
  1) sqlite backup prod -> staged full copy
  2) fanout physical cleanup apply on staged
  3) VACUUM INTO compact file
  4) rebuild memory.hnsw under staged indexes/
  5) package accept smoke
  6) verify audit table preserved if present in prod
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path

from scripts.fanout_cutover_package_accept import accept
from scripts.fanout_cutover_preflight import rebuild_memory_hnsw, vacuum_into
from scripts.fanout_physical_cleanup import CONFIRMATION, apply_cleanup, is_production_db_path


def main() -> int:
    prod = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
    out_dir = Path(
        "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
        "fanout_cleanup_full_staged"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = out_dir / "wave_memory.fanout-cleanup-full.sqlite3"
    vacuumed = out_dir / "wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
    index_dir = out_dir / "indexes"
    report_path = out_dir / "refresh_cutover_package_report.json"

    if is_production_db_path(staged):
        raise SystemExit("staged path incorrectly classified as production")

    # Snapshot production audit presence before copy.
    pc = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=120)
    prod_tabs = {r[0] for r in pc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    prod_audit = (
        pc.execute("SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events").fetchone()[0]
        if "scoped_soul_relationship_legacy_events" in prod_tabs
        else 0
    )
    prod_formal = pc.execute(
        "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships"
    ).fetchone()
    prod_marked = pc.execute(
        "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
    ).fetchone()[0]
    pc.close()

    for path in (
        staged,
        Path(str(staged) + "-wal"),
        Path(str(staged) + "-shm"),
        vacuumed,
    ):
        if path.exists():
            path.unlink()
    if index_dir.exists():
        shutil.rmtree(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    src = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=600)
    dst = sqlite3.connect(staged.as_posix(), timeout=600)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    copy_s = round(time.time() - t0, 1)
    print("COPIED", json.dumps({"size": staged.stat().st_size, "copy_seconds": copy_s}, ensure_ascii=False))

    t1 = time.time()
    clean = apply_cleanup(staged, confirmation=CONFIRMATION)
    clean_s = round(time.time() - t1, 1)
    print(
        "CLEAN",
        json.dumps(
            {
                "seconds": clean_s,
                "deleted": clean.get("memories_deleted"),
                "remaining_marked": clean.get("remaining_marked"),
                "fts_status": clean.get("fts_status"),
            },
            ensure_ascii=False,
        ),
    )

    # Ensure audit survived cleanup.
    sc = sqlite3.connect(f"file:{staged.as_posix()}?mode=ro", uri=True, timeout=120)
    stg_tabs = {r[0] for r in sc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    stg_audit_after_clean = (
        sc.execute("SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events").fetchone()[0]
        if "scoped_soul_relationship_legacy_events" in stg_tabs
        else 0
    )
    sc.close()
    if prod_audit and stg_audit_after_clean != prod_audit:
        raise SystemExit(
            f"audit lost during cleanup: prod={prod_audit} staged={stg_audit_after_clean}"
        )

    vac = vacuum_into(staged, vacuumed)
    print(
        "VACUUM",
        json.dumps(
            {
                "seconds": vac.get("seconds"),
                "source_bytes": vac.get("source_bytes"),
                "dest_bytes": vac.get("dest_bytes"),
                "saved_bytes": vac.get("saved_bytes"),
            },
            ensure_ascii=False,
        ),
    )

    idx = rebuild_memory_hnsw(vacuumed, index_dir, dimension=1024)
    print(
        "INDEX",
        json.dumps(
            {
                "seconds": idx.get("seconds"),
                "count": idx.get("count"),
                "invalid": idx.get("invalid_vectors"),
            },
            ensure_ascii=False,
        ),
    )

    acc = accept(vacuumed, index_dir / "memory.hnsw", dimension=1024)
    print("ACCEPT", json.dumps({"passed": acc.get("passed"), "verdict": acc.get("verdict")}, ensure_ascii=False))

    vc = sqlite3.connect(f"file:{vacuumed.as_posix()}?mode=ro", uri=True, timeout=120)
    vt = {r[0] for r in vc.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    vac_audit = (
        vc.execute("SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events").fetchone()[0]
        if "scoped_soul_relationship_legacy_events" in vt
        else 0
    )
    vac_formal = vc.execute(
        "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships"
    ).fetchone()
    vac_marked = vc.execute(
        "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
    ).fetchone()[0]
    vac_mem = vc.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    vac_max_ts = vc.execute("SELECT MAX(timestamp) FROM memories").fetchone()[0] or 0
    vac_max_id = vc.execute("SELECT MAX(id) FROM memories").fetchone()[0] or 0
    vc.close()

    pc = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=60)
    drift = {
        "prod_memories": pc.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "vac_memories": vac_mem,
        "prod_marked": pc.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0],
        "vac_marked": vac_marked,
        "prod_formal": list(prod_formal),
        "vac_formal": list(vac_formal),
        "prod_audit": prod_audit,
        "vac_audit": vac_audit,
        "audit_preserved": vac_audit == prod_audit,
        "prod_non_fanout_newer_ts": pc.execute(
            """SELECT COUNT(*) FROM memories
                WHERE timestamp > ?
                  AND (provenance NOT LIKE '%fanout_duplicate%' OR provenance IS NULL)""",
            (vac_max_ts,),
        ).fetchone()[0],
        "prod_ids_gt_vac_max": pc.execute(
            "SELECT COUNT(*) FROM memories WHERE id > ?",
            (vac_max_id,),
        ).fetchone()[0],
    }
    pc.close()

    report = {
        "generated_at": time.time(),
        "copy_seconds": copy_s,
        "clean_seconds": clean_s,
        "vacuum_seconds": vac.get("seconds"),
        "index_seconds": idx.get("seconds"),
        "total_seconds": round(time.time() - t0, 1),
        "clean": {
            k: clean.get(k)
            for k in (
                "planned_delete_count",
                "memories_deleted",
                "remaining_marked",
                "remaining_multi_target_families",
                "fts_status",
            )
        },
        "vacuum": {
            "dest": vac.get("dest"),
            "source_bytes": vac.get("source_bytes"),
            "dest_bytes": vac.get("dest_bytes"),
            "saved_bytes": vac.get("saved_bytes"),
        },
        "index": {
            "count": idx.get("count"),
            "invalid_vectors": idx.get("invalid_vectors"),
            "files": idx.get("files"),
        },
        "accept": acc,
        "drift_after_build": drift,
        "production_switched": False,
        "phase2_promote_allowed": False,
        "reason": "refresh_to_include_relationship_legacy_audit",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", str(report_path))
    print(json.dumps({"passed": bool(acc.get("passed")), "drift": drift}, ensure_ascii=False, indent=2))
    if not acc.get("passed") or not drift.get("audit_preserved"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
