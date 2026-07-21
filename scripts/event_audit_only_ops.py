#!/usr/bin/env python3
"""Operations helper for event_audit_only relationship history.

Default is dry-run / staged. Production apply is hard-gated:

  --apply-production --confirmation import-event-audit-only

Even then, only inserts into scoped_soul_relationship_legacy_events and
never updates scoped_soul_relationships / values / live events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from services.legacy_relationship_migration import (
    CONFIRMATION as STAGE_CONFIRMATION,
    preview,
    stage,
    _ensure_audit_tables,
    _formal_fingerprint,
    _target_scopes,
)

PROD_APPLY_CONFIRMATION = "import-event-audit-only"

DEFAULT_SCOPES = [
    {
        "bot_id": "yushu",
        "group_id": "398291136",
        "session_id": "羽书:group:398291136",
        "visibility": "group",
    },
    {
        "bot_id": "yushu",
        "group_id": "576588284",
        "session_id": "羽书:group:576588284",
        "visibility": "group",
    },
    {
        "bot_id": "yushu",
        "group_id": "1151238916",
        "session_id": "羽书:group:1151238916",
        "visibility": "group",
    },
    {
        "bot_id": "baizz",
        "group_id": "398291136",
        "session_id": "白真真:group:398291136",
        "visibility": "group",
    },
    {
        "bot_id": "yushu",
        "group_id": "28781957",
        "session_id": "羽书:group:28781957",
        "visibility": "group",
    },
    {
        "bot_id": "yushu",
        "group_id": "1018722649",
        "session_id": "羽书:group:1018722649",
        "visibility": "group",
    },
    {
        "bot_id": "yushu",
        "group_id": "286691404",
        "session_id": "羽书:group:286691404",
        "visibility": "group",
    },
]


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _is_prod_db(path: Path) -> bool:
    text = str(path).replace("\\", "/")
    return text.endswith("/wave_memory.db") and "plugin_data/astrbot_plugin_wave_memory" in text


def plan_scopes(db: Path, scopes: list[dict]) -> dict:
    conn = _ro(db)
    try:
        pre = preview(conn, scopes)
        by_scope: dict[str, dict] = {}
        for item in pre.get("events") or []:
            if item.get("disposition") != "audit":
                continue
            sc = item.get("scope") or {}
            key = f"{sc.get('bot_id')}|{sc.get('session_id')}"
            slot = by_scope.setdefault(
                key,
                {
                    "bot_id": sc.get("bot_id"),
                    "session_id": sc.get("session_id"),
                    "group_id": sc.get("group_id"),
                    "auditable": 0,
                    "subjects": set(),
                },
            )
            slot["auditable"] += 1
            slot["subjects"].add(item.get("subject"))

        scope_rows = []
        for scope in scopes:
            key = f"{scope['bot_id']}|{scope['session_id']}"
            formal = conn.execute(
                """SELECT COUNT(*) FROM scoped_soul_relationships
                    WHERE bot_id=? AND session_id=? AND visibility='group'""",
                (scope["bot_id"], scope["session_id"]),
            ).fetchone()[0]
            live_ev = conn.execute(
                """SELECT COUNT(*) FROM scoped_soul_relationship_events
                    WHERE bot_id=? AND session_id=? AND visibility='group'""",
                (scope["bot_id"], scope["session_id"]),
            ).fetchone()[0]
            zero_ev = conn.execute(
                """SELECT COUNT(*) FROM scoped_soul_relationships s
                    WHERE s.bot_id=? AND s.session_id=? AND s.visibility='group'
                      AND NOT EXISTS (
                        SELECT 1 FROM scoped_soul_relationship_events e
                         WHERE e.bot_id=s.bot_id AND e.session_id=s.session_id
                           AND e.visibility=s.visibility
                           AND e.subject_principal_id=s.subject_principal_id
                      )""",
                (scope["bot_id"], scope["session_id"]),
            ).fetchone()[0]
            existing_audit = 0
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "scoped_soul_relationship_legacy_events" in tables:
                existing_audit = conn.execute(
                    """SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events
                        WHERE bot_id=? AND session_id=? AND visibility='group'""",
                    (scope["bot_id"], scope["session_id"]),
                ).fetchone()[0]
            slot = by_scope.get(key) or {
                "auditable": 0,
                "subjects": set(),
                "bot_id": scope["bot_id"],
                "session_id": scope["session_id"],
                "group_id": scope["group_id"],
            }
            scope_rows.append(
                {
                    "bot_id": scope["bot_id"],
                    "group_id": scope["group_id"],
                    "session_id": scope["session_id"],
                    "formal": formal,
                    "live_events": live_ev,
                    "formal_subjects_zero_live_events": zero_ev,
                    "legacy_auditable_events": int(slot["auditable"]),
                    "legacy_auditable_subjects": len(slot["subjects"]),
                    "existing_audit_rows": existing_audit,
                    "net_new_audit_est": max(int(slot["auditable"]) - existing_audit, 0),
                }
            )
        return {
            "mode": "dry-run-plan",
            "db": str(db),
            "is_production_path": _is_prod_db(db),
            "preview_summary": pre.get("summary"),
            "scopes": scope_rows,
            "totals": {
                "auditable_events": sum(r["legacy_auditable_events"] for r in scope_rows),
                "formal": sum(r["formal"] for r in scope_rows),
                "zero_live_event_subjects": sum(
                    r["formal_subjects_zero_live_events"] for r in scope_rows
                ),
                "existing_audit_rows": sum(r["existing_audit_rows"] for r in scope_rows),
            },
            "production_apply_allowed_here": False,
            "production_confirmation_required": PROD_APPLY_CONFIRMATION,
            "note": "plan never writes; staged/apply are separate flags",
        }
    finally:
        conn.close()


def stage_scopes(
    source: Path,
    output: Path,
    run_dir: Path,
    scopes: list[dict],
) -> dict:
    if _is_prod_db(output):
        raise SystemExit("refusing to stage output onto production wave_memory.db path")
    if output.exists():
        output.unlink()
    source_hash = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    t0 = time.time()
    report = stage(
        source_db_path=source,
        output_db_path=output,
        run_dir=run_dir,
        target_scopes=scopes,
        expected_source_hash=source_hash,
        confirmation=STAGE_CONFIRMATION,
        mode="event_audit_only",
    )
    # post verify
    sc = sqlite3.connect(f"file:{output.as_posix()}?mode=ro", uri=True, timeout=120)
    audit_total = sc.execute(
        "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
    ).fetchone()[0]
    sc.close()
    return {
        "mode": "staged-event-audit-only",
        "seconds": round(time.time() - t0, 1),
        "output": str(output),
        "event_result": report.get("event_result"),
        "profile_result": report.get("profile_result"),
        "fingerprint_equal": report.get("formal_fingerprint_before")
        == report.get("formal_fingerprint_after"),
        "quick_check": report.get("quick_check"),
        "audit_rows_total": audit_total,
        "formal_fingerprint": report.get("formal_fingerprint_after"),
        "production_written": False,
    }


def apply_production_from_staged(
    production: Path,
    staged: Path,
    *,
    confirmation: str,
    scopes: list[dict],
) -> dict:
    if confirmation != PROD_APPLY_CONFIRMATION:
        raise SystemExit(f"confirmation_required:{PROD_APPLY_CONFIRMATION}")
    if not _is_prod_db(production):
        raise SystemExit("apply-production target is not recognized production path")
    if not staged.is_file():
        raise SystemExit(f"staged missing: {staged}")

    scopes_n = _target_scopes(scopes)
    # freeze formal fingerprint before
    prod = sqlite3.connect(production.as_posix(), timeout=600)
    prod.execute("PRAGMA busy_timeout=120000")
    try:
        before = _formal_fingerprint(prod, scopes_n)
        _ensure_audit_tables(prod)
        stg = sqlite3.connect(f"file:{staged.as_posix()}?mode=ro", uri=True, timeout=600)
        try:
            cols = [
                "legacy_event_id",
                "scope_key",
                "bot_id",
                "session_id",
                "visibility",
                "group_id",
                "subject_principal_id",
                "event_type",
                "dimension",
                "delta",
                "reason",
                "occurred_at",
                "source_episode_id",
                "source_memory_id",
                "source_hash",
                "event_hash",
                "run_id",
                "created_at",
            ]
            # restrict to requested scopes
            placeholders = ",".join("?" for _ in scopes_n)
            session_ids = [s["session_id"] for s in scopes_n]
            bot_ids = [s["bot_id"] for s in scopes_n]
            # safer: filter pairs
            rows = stg.execute(
                f"""SELECT {",".join(cols)}
                      FROM scoped_soul_relationship_legacy_events"""
            ).fetchall()
            wanted = {(s["bot_id"], s["session_id"]) for s in scopes_n}
            payload = [
                tuple(r)
                for r in rows
                if (r[2], r[3]) in wanted  # bot_id, session_id positions
            ]
            cur = prod.executemany(
                f"""INSERT OR IGNORE INTO scoped_soul_relationship_legacy_events(
                        {",".join(cols)}
                    ) VALUES ({",".join("?" for _ in cols)})""",
                payload,
            )
            inserted = int(cur.rowcount or 0)
        finally:
            stg.close()
        after = _formal_fingerprint(prod, scopes_n)
        if before != after:
            prod.rollback()
            raise SystemExit("aborted: formal fingerprint changed during audit import")
        prod.commit()
        total = prod.execute(
            "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
        ).fetchone()[0]
        return {
            "mode": "apply-production",
            "attempted_rows": len(payload),
            "inserted_or_ignored_rowcount": inserted,
            "audit_table_total": total,
            "formal_fingerprint_equal": True,
            "confirmation": confirmation,
        }
    except Exception:
        prod.rollback()
        raise
    finally:
        prod.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument("--plan", action="store_true", help="readonly production plan")
    parser.add_argument(
        "--stage-output",
        default="",
        help="if set, create event_audit_only staged DB at this path",
    )
    parser.add_argument(
        "--stage-run-dir",
        default="",
        help="run dir for stage artifacts",
    )
    parser.add_argument(
        "--apply-production",
        action="store_true",
        help="import audit rows from staged into production (gated)",
    )
    parser.add_argument("--staged-db", default="")
    parser.add_argument("--confirmation", default="")
    args = parser.parse_args()

    scopes = DEFAULT_SCOPES
    prod = Path(args.prod_db)
    report: dict = {"generated_at": time.time(), "scopes_count": len(scopes)}

    if args.plan or (not args.stage_output and not args.apply_production):
        report["plan"] = plan_scopes(prod, scopes)

    if args.stage_output:
        out = Path(args.stage_output)
        run_dir = Path(args.stage_run_dir) if args.stage_run_dir else out.parent / "run"
        report["stage"] = stage_scopes(prod, out, run_dir, scopes)

    if args.apply_production:
        report["apply"] = apply_production_from_staged(
            prod,
            Path(args.staged_db),
            confirmation=args.confirmation,
            scopes=scopes,
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
