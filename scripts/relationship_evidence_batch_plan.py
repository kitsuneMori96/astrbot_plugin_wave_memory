#!/usr/bin/env python3
"""Multi-scope readonly evidence-summary batch plan (no production writes).

Reuses single-scope ``plan()`` from relationship_evidence_summary_dryrun for every
group Scope, then emits a compact batch plan suitable for staged apply loops.

Default: no --apply. Never mutates affinity.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.relationship_evidence_summary_dryrun import plan as plan_scope


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def list_scopes(conn: sqlite3.Connection) -> list[tuple[str, str, str, int]]:
    rows = conn.execute(
        """
        SELECT bot_id, session_id, visibility, COUNT(*) AS n
          FROM scoped_soul_relationships
         WHERE visibility='group'
         GROUP BY bot_id, session_id, visibility
         ORDER BY n DESC
        """
    ).fetchall()
    return [(str(a), str(b), str(c), int(d)) for a, b, c, d in rows]


def batch_plan(
    conn: sqlite3.Connection,
    *,
    per_scope_limit: int = 0,
    max_scopes: int = 0,
    min_audit: int = 1,
) -> dict[str, Any]:
    scopes = list_scopes(conn)
    if max_scopes and max_scopes > 0:
        scopes = scopes[: int(max_scopes)]

    scope_plans: list[dict[str, Any]] = []
    total_candidates = 0
    total_formal = 0
    batches: list[dict[str, Any]] = []

    for bot_id, session_id, visibility, formal_n in scopes:
        # plan() uses full candidate list; limit only samples in report
        lim = int(per_scope_limit) if per_scope_limit and per_scope_limit > 0 else 10_000
        sp = plan_scope(conn, bot_id=bot_id, session_id=session_id, limit=lim)
        cands = [
            c
            for c in (sp.get("candidates") or [])
            if int(c.get("audit_total") or 0) >= int(min_audit)
        ]
        if per_scope_limit and per_scope_limit > 0:
            cands = cands[: int(per_scope_limit)]
        total_formal += int(sp.get("formal_rows") or formal_n)
        total_candidates += len(cands)
        scope_entry = {
            "bot_id": bot_id,
            "session_id": session_id,
            "visibility": visibility,
            "formal_rows": sp.get("formal_rows"),
            "summary_candidates": len(cands),
            "machine_evidence_rows": sp.get("machine_evidence_rows"),
            "top_audit_total": (cands[0].get("audit_total") if cands else 0),
            "sample": cands[:3],
        }
        scope_plans.append(scope_entry)
        for c in cands:
            batches.append(
                {
                    "bot_id": bot_id,
                    "session_id": session_id,
                    "visibility": "group",
                    "subject_principal_id": c["subject_principal_id"],
                    "affinity": c.get("affinity"),
                    "revision": c.get("revision"),
                    "audit_total": c.get("audit_total"),
                    "proposed_evidence_append": c.get("proposed_evidence_append"),
                }
            )

    # recommended staged order: largest scopes first, within already ordered
    return {
        "mode": "batch-plan-readonly",
        "generated_at": time.time(),
        "scope_count": len(scopes),
        "totals": {
            "formal_rows_scanned": total_formal,
            "batch_candidates": total_candidates,
            "min_audit": int(min_audit),
            "per_scope_limit": int(per_scope_limit or 0),
        },
        "scopes": scope_plans,
        "batch_size": len(batches),
        # omit full batch by default from print unless small; caller can request file
        "batch": batches,
        "writes_affinity": False,
        "writes_evidence": False,
        "phase2_promote_allowed": False,
        "recommended_staged_apply": {
            "order": "by scope formal size desc (already ordered)",
            "per_scope_chunk": 30,
            "guard": "affinity+revision match; refuse prod without --allow-prod-apply",
            "script": "relationship_evidence_summary_dryrun.py --apply --apply-db <staged>",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument(
        "--per-scope-limit",
        type=int,
        default=0,
        help="max candidates per scope (0=all candidates in scope)",
    )
    parser.add_argument("--max-scopes", type=int, default=0, help="0=all scopes")
    parser.add_argument("--min-audit", type=int, default=1)
    parser.add_argument(
        "--report",
        default="",
        help="JSON report path; full batch included",
    )
    parser.add_argument(
        "--include-batch",
        action="store_true",
        help="include full batch array in stdout (can be large)",
    )
    args = parser.parse_args()
    conn = _ro(Path(args.db))
    try:
        report = batch_plan(
            conn,
            per_scope_limit=int(args.per_scope_limit),
            max_scopes=int(args.max_scopes),
            min_audit=int(args.min_audit),
        )
    finally:
        conn.close()

    out = dict(report)
    if not args.include_batch and not args.report:
        out.pop("batch", None)
    text = json.dumps(out if args.include_batch or not args.report else {k: v for k, v in out.items() if k != "batch"}, ensure_ascii=False, indent=2)
    # Always write full report when --report set
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # stdout summary without full batch
        summary = {k: v for k, v in report.items() if k != "batch"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
