#!/usr/bin/env python3
"""Readonly inventory: formal evidence summary gaps across all Scopes.

Never writes affinity/evidence. Complements relationship_evidence_summary_dryrun
(single-scope plan) with a cheap global picture for the relationship blocked task.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.relationship_evidence_summary_dryrun import _machine_evidence


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def inventory(conn: sqlite3.Connection, *, sample_per_scope: int = 3) -> dict[str, Any]:
    tables = {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    has_audit = "scoped_soul_relationship_legacy_events" in tables
    scopes = conn.execute(
        """
        SELECT bot_id, session_id, visibility, COUNT(*) AS formal_n
          FROM scoped_soul_relationships
         WHERE visibility='group'
         GROUP BY bot_id, session_id, visibility
         ORDER BY formal_n DESC
        """
    ).fetchall()

    per_scope: list[dict[str, Any]] = []
    totals = {
        "formal": 0,
        "machine_evidence": 0,
        "has_historical_summary": 0,
        "machine_with_audit": 0,
        "summary_candidates": 0,
        "machine_without_audit": 0,
    }

    for bot_id, session_id, visibility, formal_n in scopes:
        formal_n = int(formal_n)
        totals["formal"] += formal_n
        rows = conn.execute(
            """
            SELECT subject_principal_id, affinity, evidence
              FROM scoped_soul_relationships
             WHERE bot_id=? AND session_id=? AND visibility=?
            """,
            (bot_id, session_id, visibility),
        ).fetchall()
        machine = 0
        with_summary = 0
        with_audit = 0
        without_audit = 0
        candidates_sample: list[dict[str, Any]] = []
        for subject, affinity, evidence in rows:
            raw = evidence if isinstance(evidence, str) else None
            if raw and "historical_audit_summary" in raw:
                with_summary += 1
                continue
            if not _machine_evidence(raw):
                continue
            machine += 1
            audit_n = 0
            if has_audit:
                audit_n = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events
                         WHERE bot_id=? AND session_id=? AND visibility=?
                           AND subject_principal_id=?
                        """,
                        (bot_id, session_id, visibility, subject),
                    ).fetchone()[0]
                )
            if audit_n > 0:
                with_audit += 1
                if len(candidates_sample) < sample_per_scope:
                    candidates_sample.append(
                        {
                            "subject": subject,
                            "affinity": affinity,
                            "audit_total": audit_n,
                        }
                    )
            else:
                without_audit += 1

        scope_rep = {
            "bot_id": bot_id,
            "session_id": session_id,
            "visibility": visibility,
            "formal": formal_n,
            "machine_evidence": machine,
            "has_historical_summary": with_summary,
            "summary_candidates": with_audit,
            "machine_without_audit": without_audit,
            "sample_candidates": candidates_sample,
        }
        per_scope.append(scope_rep)
        totals["machine_evidence"] += machine
        totals["has_historical_summary"] += with_summary
        totals["machine_with_audit"] += with_audit
        totals["summary_candidates"] += with_audit
        totals["machine_without_audit"] += without_audit

    return {
        "mode": "readonly-inventory",
        "generated_at": time.time(),
        "has_audit_table": has_audit,
        "scope_count": len(scopes),
        "totals": totals,
        "scopes": per_scope,
        "writes_affinity": False,
        "writes_evidence": False,
        "phase2_promote_allowed": False,
        "notes": [
            "summary_candidates = machine evidence + audit>0 + no historical_audit_summary yet",
            "production apply of evidence summary still requires user auth",
        ],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument("--sample-per-scope", type=int, default=2)
    p.add_argument("--report", default="")
    args = p.parse_args()
    conn = _ro(Path(args.db))
    try:
        report = inventory(conn, sample_per_scope=int(args.sample_per_scope))
    finally:
        conn.close()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
