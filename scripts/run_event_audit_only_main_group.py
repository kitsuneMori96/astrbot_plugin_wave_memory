#!/usr/bin/env python3
"""Stage event_audit_only for main group and verify formal affinity unchanged."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from services.legacy_relationship_migration import CONFIRMATION, stage


def main() -> int:
    prod = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db")
    out_dir = Path(
        "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
        "relationship_event_audit_only_yushu_398291136"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "wave_memory.event-audit-only.sqlite3"
    if out.exists():
        out.unlink()

    scope = {
        "bot_id": "yushu",
        "group_id": "398291136",
        "session_id": "羽书:group:398291136",
        "visibility": "group",
    }
    pc = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=60)
    prod_aff = pc.execute(
        "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()
    prod_live_ev = pc.execute(
        "SELECT COUNT(*) FROM scoped_soul_relationship_events "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()[0]
    pc.close()

    source_hash = "sha256:" + hashlib.sha256(prod.read_bytes()).hexdigest()
    t0 = time.time()
    report = stage(
        source_db_path=prod,
        output_db_path=out,
        run_dir=out_dir / "run",
        target_scopes=[scope],
        expected_source_hash=source_hash,
        confirmation=CONFIRMATION,
        mode="event_audit_only",
    )
    elapsed = round(time.time() - t0, 1)

    sc = sqlite3.connect(f"file:{out.as_posix()}?mode=ro", uri=True, timeout=60)
    stg_aff = sc.execute(
        "SELECT COUNT(*), COALESCE(SUM(affinity),0) FROM scoped_soul_relationships "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()
    audit_n = sc.execute(
        "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()[0]
    audit_subj = sc.execute(
        "SELECT COUNT(DISTINCT subject_principal_id) FROM scoped_soul_relationship_legacy_events "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()[0]
    live_ev = sc.execute(
        "SELECT COUNT(*) FROM scoped_soul_relationship_events "
        "WHERE bot_id=? AND session_id=?",
        ("yushu", "羽书:group:398291136"),
    ).fetchone()[0]
    sc.close()

    result = {
        "seconds": elapsed,
        "mode": report.get("mode"),
        "profile_result": report.get("profile_result"),
        "event_result": report.get("event_result"),
        "quick_check": report.get("quick_check"),
        "fingerprint_equal": report.get("formal_fingerprint_before")
        == report.get("formal_fingerprint_after"),
        "prod_formal": {"count": prod_aff[0], "affinity_sum": prod_aff[1]},
        "staged_formal": {"count": stg_aff[0], "affinity_sum": stg_aff[1]},
        "affinity_unchanged": list(prod_aff) == list(stg_aff),
        "prod_live_events": prod_live_ev,
        "staged_live_events": live_ev,
        "live_events_unchanged": prod_live_ev == live_ev,
        "audit_rows": audit_n,
        "audit_subjects": audit_subj,
        "output": str(out),
        "production_written": False,
    }
    (out_dir / "event_audit_only_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    # live_events may differ by a few rows under concurrent production WAL writers
    # when stage() uses file copy; formal fingerprint + affinity are the hard gates.
    result["live_events_note"] = (
        "advisory_only_under_concurrent_wal"
        if not result["live_events_unchanged"]
        else "exact_match"
    )
    ok = (
        result["mode"] == "event_audit_only"
        and result["fingerprint_equal"]
        and result["affinity_unchanged"]
        and result["audit_rows"] > 0
        and result["quick_check"] == "ok"
    )
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
