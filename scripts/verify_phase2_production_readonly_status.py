#!/usr/bin/env python3
"""Readonly production status for Phase2 / grants / relationship evidence.

Never writes. Never promotes. Never cutovers.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def verify(prod: Path, *, vacuumed: Path | None = None) -> dict:
    conn = _ro(prod)
    try:
        tables = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        marked = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
        formal = int(conn.execute("SELECT COUNT(*) FROM scoped_soul_relationships").fetchone()[0])
        audit = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                ).fetchone()[0]
            )
            if "scoped_soul_relationship_legacy_events" in tables
            else -1
        )
        grants = (
            int(conn.execute("SELECT COUNT(*) FROM shared_memory_grants").fetchone()[0])
            if "shared_memory_grants" in tables
            else -1
        )
        evidence_summary = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
        main_formal = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE bot_id='yushu' AND session_id='羽书:group:398291136'
                   AND visibility='group'
                """
            ).fetchone()[0]
        )
        multi_families = (
            int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT 1 FROM scope_recovery_memory_map
                         GROUP BY legacy_memory_id HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )
            if "scope_recovery_memory_map" in tables
            else -1
        )
    finally:
        conn.close()

    vac = None
    if vacuumed and vacuumed.is_file():
        vc = _ro(vacuumed)
        try:
            vac = {
                "path": str(vacuumed),
                "exists": True,
                "marked": int(
                    vc.execute(
                        "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
                    ).fetchone()[0]
                ),
                "formal": int(
                    vc.execute("SELECT COUNT(*) FROM scoped_soul_relationships").fetchone()[0]
                ),
                "audit": int(
                    vc.execute(
                        "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                    ).fetchone()[0]
                )
                if "scoped_soul_relationship_legacy_events"
                in {
                    str(r[0])
                    for r in vc.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                else -1,
            }
        finally:
            vc.close()

    report = {
        "mode": "readonly-status",
        "generated_at": time.time(),
        "prod": {
            "path": str(prod),
            "marked_fanout": marked,
            "formal": formal,
            "main_formal_yushu_398291136": main_formal,
            "audit_rows": audit,
            "shared_memory_grants": grants,
            "evidence_historical_summary_rows": evidence_summary,
            "multi_target_families": multi_families,
        },
        "vacuumed_package": vac,
        "invariants": {
            "phase2_promote_allowed": False,
            "prod_has_no_grants_table_or_empty": grants in (-1, 0),
            "prod_has_no_evidence_summary_writes": evidence_summary == 0,
            "audit_present": audit > 0,
        },
        "operator_next_requires_user_auth": [
            "fanout cutover apply (confirmation cutover-fanout-cleaned-db)",
            "same-bot grants production apply (confirmation grant-from-fanout-map + allow-prod)",
            "shared_memory_grants_enabled=true in Cross_Group_Settings",
            "evidence summary production apply (--allow-prod-apply)",
        ],
    }
    report["ok"] = all(
        [
            report["invariants"]["phase2_promote_allowed"] is False,
            report["invariants"]["prod_has_no_grants_table_or_empty"],
            report["invariants"]["prod_has_no_evidence_summary_writes"],
            report["invariants"]["audit_present"],
        ]
    )
    return report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument(
        "--vacuumed",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
        ),
    )
    p.add_argument("--report", default="")
    args = p.parse_args()
    report = verify(Path(args.db), vacuumed=Path(args.vacuumed) if args.vacuumed else None)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
