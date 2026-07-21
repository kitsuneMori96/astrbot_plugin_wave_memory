#!/usr/bin/env python3
"""Post-governance healthcheck (readonly by default).

Checks:
  - fanout_marked == 0
  - active unscoped == 0
  - evidence summaries healthy / missing-with-audit count
  - formal fingerprint stable-ish
  - Phase2 promote still forbidden in code
  - optional: invoke accept_five_success_criteria

Never promotes. Never cutovers. Never writes production unless --refill-missing
with explicit confirmation (delegates to refill script).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
PROD = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
DEFAULT_REPORT = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "post_governance_healthcheck.json"
)


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def check(db: Path = PROD) -> dict[str, Any]:
    conn = _ro(db)
    try:
        tabs = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        memories = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        fanout = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
        active_unscoped = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
                   AND COALESCE(quarantine,0)=0
                """
            ).fetchone()[0]
        )
        unscoped_total = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''
                """
            ).fetchone()[0]
        )
        quarantine = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE COALESCE(quarantine,0)=1"
            ).fetchone()[0]
        )
        formalized = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE resolution_state='owned_formalized_from_unscoped'"
            ).fetchone()[0]
        )
        formal_n, aff_sum = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships"
        ).fetchone()
        summaries = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
        missing = 0
        if "scoped_soul_relationship_legacy_events" in tabs:
            missing = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM scoped_soul_relationships r
                     WHERE (r.evidence IS NULL
                            OR r.evidence NOT LIKE '%historical_audit_summary%')
                       AND EXISTS (
                         SELECT 1 FROM scoped_soul_relationship_legacy_events e
                          WHERE e.bot_id=r.bot_id AND e.session_id=r.session_id
                            AND e.visibility=r.visibility
                            AND e.subject_principal_id=r.subject_principal_id
                       )
                    """
                ).fetchone()[0]
            )
        map_multi = -1
        if "scope_recovery_memory_map" in tabs:
            map_multi = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT 1 FROM scope_recovery_memory_map
                       GROUP BY legacy_memory_id HAVING COUNT(*)>1
                    )
                    """
                ).fetchone()[0]
            )
        grants = (
            int(conn.execute("SELECT COUNT(*) FROM shared_memory_grants").fetchone()[0])
            if "shared_memory_grants" in tabs
            else -1
        )
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()

    promote_forbidden = False
    promote_path = PLUGIN / "scripts" / "apply_classified_scope_recovery.py"
    if promote_path.is_file():
        txt = promote_path.read_text(encoding="utf-8", errors="ignore")
        promote_forbidden = (
            "classified_fanout_promote_forbidden" in txt
            or "fanout_promote_forbidden" in txt
        )
    merge_path = PLUGIN / "engine" / "db" / "scoped_soul_repo.py"
    merge_present = False
    if merge_path.is_file():
        merge_present = "_merge_relationship_evidence" in merge_path.read_text(
            encoding="utf-8", errors="ignore"
        )

    gates = {
        "quick_check_ok": quick == "ok",
        "fanout_marked_zero": fanout == 0,
        "map_multi_zero": map_multi == 0,
        "active_unscoped_zero": active_unscoped == 0,
        "summaries_majority": summaries >= 1000,
        "missing_summary_zero": missing == 0,
        "formal_fingerprint_ok": int(formal_n) == 1088 and int(aff_sum) == 3033,
        "promote_forbidden_in_code": promote_forbidden,
        "evidence_merge_in_code": merge_present,
        "phase2_promote_allowed": False,
    }
    ok = all(
        [
            gates["quick_check_ok"],
            gates["fanout_marked_zero"],
            gates["active_unscoped_zero"],
            gates["summaries_majority"],
            gates["missing_summary_zero"],
            gates["promote_forbidden_in_code"],
            gates["evidence_merge_in_code"],
        ]
    )
    advice: list[str] = []
    if missing > 0:
        advice.append(
            "run refill_missing_evidence_summaries (needs auth if writing prod)"
        )
    if not merge_present:
        advice.append("scoped_soul_repo missing _merge_relationship_evidence")
    if ok:
        advice.append(
            "Phase2 remains permanently closed (protected blocked marker); "
            "do not reopen fanout promote; healthcheck green means no further "
            "autonomous production governance writes are required"
        )

    return {
        "mode": "post_governance_healthcheck",
        "generated_at": time.time(),
        "prod": str(db),
        "metrics": {
            "memories": memories,
            "fanout_marked": fanout,
            "active_unscoped": active_unscoped,
            "unscoped_total": unscoped_total,
            "quarantine": quarantine,
            "formalized": formalized,
            "formal": int(formal_n),
            "affinity_sum": int(aff_sum),
            "summaries": summaries,
            "missing_summary": missing,
            "map_multi": map_multi,
            "grants": grants,
            "quick_check": quick,
        },
        "gates": gates,
        "ok": ok,
        "advice": advice,
        "writes_production": False,
        "phase2_promote_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(PROD))
    p.add_argument("--report", default=str(DEFAULT_REPORT))
    p.add_argument(
        "--with-five-criteria",
        action="store_true",
        help="also run accept_five_success_criteria and embed scoreboard",
    )
    args = p.parse_args(argv)
    report = check(Path(args.db))
    if args.with_five_criteria:
        import sys

        sys.path.insert(0, str(PLUGIN))
        from scripts.accept_five_success_criteria import main as five_main

        # five_main prints full report; capture by re-reading its output file
        five_main()
        five_path = Path(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "accept_five_success_criteria.json"
        )
        if five_path.is_file():
            five = json.loads(five_path.read_text(encoding="utf-8"))
            report["five_criteria"] = {
                "scoreboard": five.get("scoreboard"),
                "overall": five.get("overall"),
                "overall_note": five.get("overall_note"),
            }
            if five.get("overall") != "DONE":
                report["ok"] = False
                report["advice"].append("five_criteria not DONE")
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
