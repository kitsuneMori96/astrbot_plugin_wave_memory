#!/usr/bin/env python3
"""One-shot readonly readiness preflight after Wave1/2 and before Wave3 auth.

Never writes production. Never promotes. Never cutovers.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def preflight(
    prod: Path,
    *,
    formalize_plan: Path | None = None,
    evidence_apply: Path | None = None,
) -> dict[str, Any]:
    conn = _ro(prod)
    try:
        tables = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        memories = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        marked = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
        unscoped = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''
                """
            ).fetchone()[0]
        )
        formal_n, aff_sum = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships"
        ).fetchone()
        formal_n = int(formal_n)
        aff_sum = int(aff_sum)
        summaries = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
        audit_missing_summary = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships r
                 WHERE (r.evidence IS NULL OR r.evidence NOT LIKE '%historical_audit_summary%')
                   AND EXISTS (
                     SELECT 1 FROM scoped_soul_relationship_legacy_events e
                      WHERE e.bot_id=r.bot_id AND e.session_id=r.session_id
                        AND e.visibility=r.visibility
                        AND e.subject_principal_id=r.subject_principal_id
                   )
                """
            ).fetchone()[0]
        ) if "scoped_soul_relationship_legacy_events" in tables else -1
        audit = (
            int(conn.execute("SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events").fetchone()[0])
            if "scoped_soul_relationship_legacy_events" in tables
            else -1
        )
        grants = (
            int(conn.execute("SELECT COUNT(*) FROM shared_memory_grants").fetchone()[0])
            if "shared_memory_grants" in tables
            else -1
        )
        multi = (
            int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                      SELECT 1 FROM scope_recovery_memory_map
                       GROUP BY legacy_memory_id HAVING COUNT(*)>1
                    )
                    """
                ).fetchone()[0]
            )
            if "scope_recovery_memory_map" in tables
            else -1
        )
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()

    plan_meta: dict[str, Any] = {}
    if formalize_plan and formalize_plan.is_file():
        try:
            plan_meta = json.loads(formalize_plan.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            plan_meta = {"load_error": str(exc)[:160]}

    prev_sum = None
    if evidence_apply and evidence_apply.is_file():
        try:
            prev = json.loads(evidence_apply.read_text(encoding="utf-8"))
            prev_sum = prev.get("summaries_after")
        except Exception:
            prev_sum = None

    checks = {
        "quick_check_ok": quick == "ok",
        "fanout_marked_zero": marked == 0,
        "multi_family_map_zero": multi == 0,
        "formal_intact": formal_n == 1088 and aff_sum == 3033,
        "audit_present": audit == 91339,
        "evidence_summaries_majority": summaries >= 1000,
        "pre_cutover_backup_present": Path(
            str(prod.parent / "wave_memory.pre_cutover_1784589219.db")
        ).is_file()
        or any(prod.parent.glob("wave_memory.pre_cutover_*.db")),
        "grants_not_auto_filled": grants in (0, -1),
        "phase2_promote_allowed": False,
    }
    # soft note: live relationship upserts may rewrite evidence without summary
    notes = []
    if prev_sum is not None and summaries < int(prev_sum):
        notes.append(
            f"evidence_summary_drift:{prev_sum}->{summaries} "
            f"(likely live affinity/evidence rewrite; audit_missing_summary={audit_missing_summary})"
        )
    if unscoped > 0:
        notes.append(f"unscoped_remaining:{unscoped}")
    if formalize_plan and plan_meta:
        notes.append(
            f"formalize_plan_candidates:{plan_meta.get('candidate_count')}"
        )

    ready_for_formalize_auth = all(
        [
            checks["quick_check_ok"],
            checks["fanout_marked_zero"],
            checks["formal_intact"],
            checks["audit_present"],
        ]
    )
    report = {
        "mode": "wave_governance_readiness_preflight",
        "generated_at": time.time(),
        "prod": str(prod),
        "metrics": {
            "memories": memories,
            "fanout_marked": marked,
            "unscoped": unscoped,
            "formal": formal_n,
            "affinity_sum": aff_sum,
            "audit": audit,
            "evidence_summaries": summaries,
            "audit_missing_summary": audit_missing_summary,
            "grants": grants,
            "multi_family_map": multi,
            "quick_check": quick,
        },
        "checks": checks,
        "ready_for_formalize_auth": ready_for_formalize_auth,
        "not_ready_for_grants_from_fanout_map": True,
        "phase2_fanout_promote": "permanently_closed",
        "auth_needed_to_write": [
            "授权补 3 条 evidence 摘要",
            "授权 unscoped owned formalize 试点",
            "填写 scope_map 并授权 formalize 这些群（hold 群）",
        ],
        "notes": notes,
        "writes_production": False,
        "ok": ready_for_formalize_auth and checks["fanout_marked_zero"],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument(
        "--formalize-plan",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "unscoped_owned_formalize_pilot/prod_ready_batch_plan_summary.json"
        ),
    )
    p.add_argument(
        "--evidence-apply-report",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "relationship_evidence_prod_apply.json"
        ),
    )
    p.add_argument(
        "--report",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "wave_governance_readiness_preflight.json"
        ),
    )
    args = p.parse_args(argv)
    report = preflight(
        Path(args.prod_db),
        formalize_plan=Path(args.formalize_plan) if args.formalize_plan else None,
        evidence_apply=Path(args.evidence_apply_report)
        if args.evidence_apply_report
        else None,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
