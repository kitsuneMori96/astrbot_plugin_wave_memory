#!/usr/bin/env python3
"""Smoke: formal evidence_summaries extractable from staged pilot slices.

Read-only against staged DBs and production. Never writes production.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from services.relationship_evidence_display import extract_historical_audit_summaries


def _count_summaries(db: Path) -> dict:
    if not db.is_file():
        return {"path": str(db), "exists": False}
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = {
            str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "scoped_soul_relationships" not in tables:
            return {"path": str(db), "exists": True, "error": "no_relationships_table"}
        rows = conn.execute(
            "SELECT subject_principal_id, evidence FROM scoped_soul_relationships"
        ).fetchall()
        with_summary = 0
        sample = []
        for subject, evidence in rows:
            texts = extract_historical_audit_summaries(evidence, max_items=1)
            if texts:
                with_summary += 1
                if len(sample) < 2:
                    sample.append({"subject": subject, "summary": texts[0][:120]})
        return {
            "path": str(db),
            "exists": True,
            "formal": len(rows),
            "with_evidence_summary": with_summary,
            "sample": sample,
        }
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--staged-glob-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "relationship_evidence_multi_scope_pilot"
        ),
    )
    p.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument("--report", default="")
    args = p.parse_args()
    staged_dir = Path(args.staged_glob_dir)
    slices = sorted(staged_dir.glob("slice_*.sqlite3")) if staged_dir.is_dir() else []
    report = {
        "staged_slices": [_count_summaries(path) for path in slices[:12]],
        "prod": _count_summaries(Path(args.prod_db)),
        "writes_production": False,
        "phase2_promote_allowed": False,
    }
    # Invariants for this smoke
    prod_n = int(report["prod"].get("with_evidence_summary") or 0)
    staged_total = sum(int(s.get("with_evidence_summary") or 0) for s in report["staged_slices"])
    report["ok"] = prod_n == 0 and staged_total > 0
    report["staged_with_summary_total"] = staged_total
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())