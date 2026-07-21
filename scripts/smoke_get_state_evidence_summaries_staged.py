#!/usr/bin/env python3
"""Smoke relationship.evidence_summaries via get_state + staged extract.

1) Full-schema temp DB: upsert relationship with summary → get_state returns evidence_summaries
2) Staged pilot slices: extract summaries from evidence column (same helper as get_state)
3) Production RO: still 0 summary rows

Never writes production.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

PLUGIN = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
PILOT = Path(
    "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
    "relationship_evidence_multi_scope_pilot"
)
PROD = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
OUT = PILOT / "smoke_get_state_evidence_summaries.json"


def main() -> int:
    sys.path.insert(0, str(PLUGIN))
    from domain.scope import RuntimeScope, SessionRef
    from engine.db.connection import ConnectionManager
    from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
    from engine.db.scoped_soul_repo import ScopedSoulRepository
    from services.relationship_evidence_display import extract_historical_audit_summaries

    report: dict = {
        "mode": "smoke_get_state_evidence_summaries",
        "generated_at": time.time(),
        "writes_production": False,
        "phase2_promote_allowed": False,
    }

    # --- 1) full-schema get_state path ---
    tmp = Path(tempfile.mkdtemp()) / "soul_full.db"
    cm = ConnectionManager(str(tmp))
    try:
        ensure_scoped_soul_schema(cm)
        repo = ScopedSoulRepository(cm)
        scope = RuntimeScope(
            bot_id="yushu",
            visibility="group",
            session=SessionRef(
                id="羽书:group:398291136",
                platform_id="羽书",
                kind="group",
                conversation_id="398291136",
            ),
            subject_principal_id="羽书:user:1353245454",
        )
        summary_text = "历史审计事件 9 条；类型：direct_reply×9；（只读摘要，不影响亲和分）"
        repo.upsert_relationship(
            scope,
            subject_principal_id="羽书:user:1353245454",
            affinity=9,
            state="neutral",
            dimensions={
                "familiarity": 9,
                "trust": 0,
                "fun": 0,
                "hostility": 0,
                "depth": 0,
            },
            evidence=[
                {"relationship_event_id": 1},
                {
                    "kind": "historical_audit_summary",
                    "summary": summary_text,
                    "affects_affinity": False,
                },
            ],
        )
        state = repo.get_state(
            scope, subject_principal_id="羽书:user:1353245454", limit=25, offset=0
        )
        rel = state.get("relationship") or {}
        summaries = rel.get("evidence_summaries") or []
        listed = repo.list_relationships(
            scope, subject_principal_id="羽书:user:1353245454"
        )
        list_sum = (listed[0].get("evidence_summaries") if listed else None) or []
        report["get_state_full_schema"] = {
            "affinity": rel.get("affinity"),
            "summaries_n": len(summaries),
            "list_summaries_n": len(list_sum),
            "sample": (summaries[0][:100] if summaries else ""),
            "ok": (
                rel.get("affinity") == 9
                and len(summaries) >= 1
                and summary_text in summaries[0]
                and len(list_sum) >= 1
            ),
        }
    finally:
        cm.close()

    # --- 2) staged slices: same helper as get_state uses ---
    slices = sorted(PILOT.glob("slice_*.sqlite3"))
    staged = []
    staged_ok = 0
    for path in slices:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                str(r[0])
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "scoped_soul_relationships" not in tables:
                staged.append({"path": str(path), "error": "no_table"})
                continue
            rows = conn.execute(
                "SELECT subject_principal_id, affinity, evidence "
                "FROM scoped_soul_relationships"
            ).fetchall()
            with_sum = 0
            sample = None
            for subject, aff, evidence in rows:
                texts = extract_historical_audit_summaries(evidence, max_items=1)
                if texts:
                    with_sum += 1
                    if sample is None:
                        sample = {
                            "subject": subject,
                            "affinity": aff,
                            "summary": texts[0][:100],
                        }
            ok = with_sum > 0
            if ok:
                staged_ok += 1
            staged.append(
                {
                    "path": path.name,
                    "formal": len(rows),
                    "with_summary": with_sum,
                    "sample": sample,
                    "ok": ok,
                }
            )
        finally:
            conn.close()
    report["staged_extract"] = {
        "slices_total": len(staged),
        "slices_with_summary": staged_ok,
        "slices": staged,
        "ok": staged_ok >= 1,
    }

    # --- 3) production still zero ---
    pc = sqlite3.connect(f"file:{PROD.as_posix()}?mode=ro", uri=True)
    try:
        prod_n = pc.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationships "
            "WHERE evidence LIKE '%historical_audit_summary%'"
        ).fetchone()[0]
    finally:
        pc.close()
    report["prod_evidence_summary_rows"] = int(prod_n)

    report["ok"] = bool(
        report["get_state_full_schema"]["ok"]
        and report["staged_extract"]["ok"]
        and int(prod_n) == 0
    )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
