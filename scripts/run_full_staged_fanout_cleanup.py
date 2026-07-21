#!/usr/bin/env python3
"""Apply fanout cleanup on an existing full staged DB copy (never production)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from scripts.fanout_physical_cleanup import CONFIRMATION, apply_cleanup, plan_cleanup


def main() -> int:
    staged = Path(
        "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
        "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.sqlite3"
    )
    prod = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
    out_dir = staged.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Always rebuild staged from current production for a clean full run.
    if staged.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(staged) + suffix) if suffix else staged
            if p.exists():
                p.unlink()
    t_copy = time.time()
    src = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=600)
    dst = sqlite3.connect(staged.as_posix(), timeout=600)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    print(
        "COPIED",
        json.dumps(
            {
                "path": str(staged),
                "size": staged.stat().st_size,
                "copy_seconds": round(time.time() - t_copy, 1),
            },
            ensure_ascii=False,
        ),
    )

    pre_conn = sqlite3.connect(staged.as_posix(), timeout=120)
    pre = {
        "quick": pre_conn.execute("PRAGMA quick_check").fetchone()[0],
        "memories": pre_conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "marked": pre_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0],
    }
    pre_conn.close()
    print("PRE", json.dumps(pre, ensure_ascii=False))

    t0 = time.time()
    plan = plan_cleanup(staged)
    print(
        "PLAN",
        json.dumps(
            {
                "delete_count": plan["delete_count"],
                "apply_allowed_here": plan["apply_allowed_here"],
                "is_production_path": plan["is_production_path"],
            },
            ensure_ascii=False,
        ),
    )
    result = apply_cleanup(staged, confirmation=CONFIRMATION)
    elapsed = round(time.time() - t0, 1)
    print("APPLY_SECONDS", elapsed)
    print(
        json.dumps(
            {k: result[k] for k in result if k != "cascade_deleted"},
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        "CASCADE_TOP",
        dict(sorted(result.get("cascade_deleted", {}).items(), key=lambda kv: -kv[1])[:8]),
    )

    post_conn = sqlite3.connect(f"file:{staged.as_posix()}?mode=ro", uri=True, timeout=120)
    post = {
        "quick": post_conn.execute("PRAGMA quick_check").fetchone()[0],
        "memories": post_conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "marked": post_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0],
        "map": post_conn.execute("SELECT COUNT(*) FROM scope_recovery_memory_map").fetchone()[0],
        "multi": post_conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT 1 FROM scope_recovery_memory_map
                    GROUP BY legacy_memory_id HAVING COUNT(*) > 1
               )"""
        ).fetchone()[0],
        "formal": post_conn.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships"
        ).fetchone()[0],
    }
    post_conn.close()
    print("POST", json.dumps(post, ensure_ascii=False, indent=2))

    prod_conn = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=60)
    prod_stats = {
        "memories": prod_conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "marked": prod_conn.execute(
            "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
        ).fetchone()[0],
    }
    prod_conn.close()
    print("PROD", json.dumps(prod_stats, ensure_ascii=False))

    report = {
        "elapsed_seconds": elapsed,
        "pre": pre,
        "plan": {
            "delete_count": plan["delete_count"],
            "marked_rows": plan["marked_rows"],
            "cascade_counts": plan["cascade_counts"],
            "is_production_path": plan["is_production_path"],
        },
        "apply": result,
        "post": post,
        "prod": prod_stats,
    }
    report_path = out_dir / "full_staged_cleanup_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
