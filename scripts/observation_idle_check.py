#!/usr/bin/env python3
"""Readonly observation-idle drift check (no destructive, no fanout).

Reports whether production memory retrieval is still healthy enough to stay
in observation mode without autonomous cleanup work.

Exit 0 when idle (no actionable drift), 1 when drift/problems detected.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


def _ensure_path() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _active_sql() -> str:
    return (
        "COALESCE(quarantine, 0)=0 "
        "AND COALESCE(memory_type, 'message') NOT IN "
        "('archived', 'evicted', 'deleted', 'noise') "
        "AND COALESCE(source, '') != 'noise'"
    )


def check_db(conn: sqlite3.Connection) -> dict[str, Any]:
    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    total = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    active = int(conn.execute(f"SELECT COUNT(*) FROM memories WHERE {_active_sql()}").fetchone()[0])
    soft_deleted = int(
        conn.execute("SELECT COUNT(*) FROM memories WHERE memory_type='deleted'").fetchone()[0]
    )
    dual_1h = conn.execute(
        f"""
        SELECT COUNT(*), COALESCE(SUM(n), 0) FROM (
          SELECT group_id, sender_id, substr(content, 1, 100) AS c, COUNT(*) AS n
            FROM memories
           WHERE {_active_sql()}
             AND timestamp > (strftime('%s', 'now') - 3600)
             AND content != ''
             AND group_id GLOB '[0-9]*'
             AND COALESCE(bot_id, '') != ''
           GROUP BY 1, 2, 3
          HAVING COUNT(DISTINCT bot_id) >= 2
        )
        """
    ).fetchone()
    return {
        "quick_check": quick,
        "total": total,
        "active": active,
        "soft_deleted": soft_deleted,
        "dual_bot_1h": {"buckets": int(dual_1h[0] or 0), "rows": int(dual_1h[1] or 0)},
    }


def check_hnsw(conn: sqlite3.Connection, index_dir: Path) -> dict[str, Any]:
    manifest_path = index_dir / "memory.hnsw.manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": "manifest_missing"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _ensure_path()
    from engine.vector_index import VectorIndex

    dim = int(manifest.get("dimension") or 1024)
    count = int(manifest.get("count") or 0)
    idx = VectorIndex(
        dimension=dim,
        max_elements=max(count, 1),
        index_path=str(index_dir / "memory.hnsw"),
        kind="memory",
        allow_resize=False,
        strict_manifest=True,
    )
    get_ids = getattr(idx.index, "get_ids_list", None)
    ids = [int(x) for x in (list(get_ids()) if callable(get_ids) else [])]
    inactive = 0
    for offset in range(0, len(ids), 2000):
        chunk = ids[offset : offset + 2000]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        inactive += int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM memories
                 WHERE id IN ({placeholders})
                   AND (
                        COALESCE(quarantine, 0) != 0
                     OR COALESCE(memory_type, 'message') IN
                        ('archived', 'evicted', 'deleted', 'noise')
                   )
                """,
                chunk,
            ).fetchone()[0]
        )
    ratio = (inactive / len(ids)) if ids else 0.0
    return {
        "ok": ratio <= 0.05 and count > 0,
        "generation": int(manifest.get("generation") or 0),
        "count": count,
        "loaded": len(ids),
        "inactive": inactive,
        "inactive_ratio": round(ratio, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"),
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory"),
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    report: dict[str, Any] = {
        "generated_at": time.time(),
        "mode": "observation_idle_check",
        "destructive_allowed": False,
        "fanout_allowed": False,
        "db": str(args.db),
        "index_dir": str(args.index_dir),
    }
    if not args.db.is_file():
        report["ok"] = False
        report["error"] = "db_missing"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    conn = _ro(args.db)
    try:
        report["db_stats"] = check_db(conn)
        report["hnsw"] = check_hnsw(conn, args.index_dir)
    finally:
        conn.close()

    db_ok = report["db_stats"].get("quick_check") == "ok"
    hnsw_ok = bool(report["hnsw"].get("ok"))
    # Dual-bot near-window is informational only (expected dual persona).
    idle = db_ok and hnsw_ok
    report["ok"] = idle
    report["verdict"] = "observation_idle" if idle else "drift_or_unhealthy"
    report["actionable_without_auth"] = False if idle else [
        "investigate_hnsw_or_db",
        "do_not_destructive_without_auth",
    ]

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "verdict": report["verdict"],
                    "wrote": str(args.out),
                    "hnsw_inactive": report["hnsw"].get("inactive"),
                    "dual_bot_1h": report["db_stats"].get("dual_bot_1h"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0 if idle else 1


if __name__ == "__main__":
    raise SystemExit(main())
