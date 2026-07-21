#!/usr/bin/env python3
"""Insert-only promote of missing formal relationships from a staged DB.

Safety rules:
- Never UPDATE existing production formal relationships.
- Only INSERT subjects present in staged and absent in production for one exact Scope.
- Copy matching relationship_values and relationship revision rows for those subjects.
- Do not touch legacy relationship_events or Phase-2 fanout paths.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn = sqlite3.connect(path.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _subjects(conn: sqlite3.Connection, *, bot_id: str, session_id: str) -> set[str]:
    rows = conn.execute(
        """SELECT subject_principal_id FROM scoped_soul_relationships
           WHERE bot_id=? AND session_id=? AND visibility='group'""",
        (bot_id, session_id),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def plan_fill(
    production: Path,
    staged: Path,
    *,
    bot_id: str,
    session_id: str,
) -> dict:
    prod = _connect(production, readonly=True)
    stg = _connect(staged, readonly=True)
    try:
        prod_subjects = _subjects(prod, bot_id=bot_id, session_id=session_id)
        staged_subjects = _subjects(stg, bot_id=bot_id, session_id=session_id)
        missing = sorted(staged_subjects - prod_subjects)
        rows = []
        if missing:
            placeholders = ",".join("?" for _ in missing)
            rows = stg.execute(
                f"""SELECT subject_principal_id, affinity, state, dimensions, revision, evidence, updated_at
                      FROM scoped_soul_relationships
                     WHERE bot_id=? AND session_id=? AND visibility='group'
                       AND subject_principal_id IN ({placeholders})
                     ORDER BY subject_principal_id""",
                (bot_id, session_id, *missing),
            ).fetchall()
        value_count = 0
        rev_count = 0
        if missing:
            placeholders = ",".join("?" for _ in missing)
            value_count = stg.execute(
                f"""SELECT COUNT(*) FROM scoped_soul_relationship_values
                    WHERE bot_id=? AND session_id=? AND visibility='group'
                      AND subject_principal_id IN ({placeholders})""",
                (bot_id, session_id, *missing),
            ).fetchone()[0]
            rev_count = stg.execute(
                f"""SELECT COUNT(*) FROM scoped_soul_revisions
                    WHERE bot_id=? AND session_id=? AND visibility='group'
                      AND component='relationship'
                      AND subject_principal_id IN ({placeholders})""",
                (bot_id, session_id, *missing),
            ).fetchone()[0]
        return {
            "mode": "dry-run",
            "bot_id": bot_id,
            "session_id": session_id,
            "production_count": len(prod_subjects),
            "staged_count": len(staged_subjects),
            "missing_count": len(missing),
            "values_to_copy": int(value_count or 0),
            "revisions_to_copy": int(rev_count or 0),
            "missing_subjects_preview": [
                {
                    "subject_principal_id": r[0],
                    "affinity": r[1],
                    "state": r[2],
                    "dimensions": r[3],
                }
                for r in rows[:20]
            ],
            "missing_subjects": missing,
        }
    finally:
        prod.close()
        stg.close()


def apply_fill(
    production: Path,
    staged: Path,
    *,
    bot_id: str,
    session_id: str,
    backup_path: Path,
) -> dict:
    plan = plan_fill(production, staged, bot_id=bot_id, session_id=session_id)
    missing = plan["missing_subjects"]
    if not missing:
        plan["mode"] = "apply"
        plan["inserted_relationships"] = 0
        plan["inserted_values"] = 0
        plan["inserted_revisions"] = 0
        plan["backup_path"] = None
        return plan

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise SystemExit(f"backup already exists: {backup_path}")
    # Lightweight logical backup of the exact Scope rows before mutation.
    prod_ro = _connect(production, readonly=True)
    try:
        rel_rows = prod_ro.execute(
            """SELECT * FROM scoped_soul_relationships
               WHERE bot_id=? AND session_id=? AND visibility='group'""",
            (bot_id, session_id),
        ).fetchall()
        val_rows = prod_ro.execute(
            """SELECT * FROM scoped_soul_relationship_values
               WHERE bot_id=? AND session_id=? AND visibility='group'""",
            (bot_id, session_id),
        ).fetchall()
        rev_rows = prod_ro.execute(
            """SELECT * FROM scoped_soul_revisions
               WHERE bot_id=? AND session_id=? AND visibility='group'
                 AND component='relationship'""",
            (bot_id, session_id),
        ).fetchall()
        backup_payload = {
            "created_at": time.time(),
            "bot_id": bot_id,
            "session_id": session_id,
            "policy": "fill_missing_only",
            "relationships": [list(r) for r in rel_rows],
            "values": [list(r) for r in val_rows],
            "revisions": [list(r) for r in rev_rows],
            "relationship_columns": [r[1] for r in prod_ro.execute("PRAGMA table_info(scoped_soul_relationships)")],
            "value_columns": [r[1] for r in prod_ro.execute("PRAGMA table_info(scoped_soul_relationship_values)")],
            "revision_columns": [r[1] for r in prod_ro.execute("PRAGMA table_info(scoped_soul_revisions)")],
        }
        backup_path.write_text(json.dumps(backup_payload, ensure_ascii=False), encoding="utf-8")
    finally:
        prod_ro.close()

    stg = _connect(staged, readonly=True)
    prod = _connect(production, readonly=False)
    inserted_rel = inserted_val = inserted_rev = 0
    try:
        placeholders = ",".join("?" for _ in missing)
        rels = stg.execute(
            f"""SELECT bot_id, session_id, visibility, subject_principal_id,
                       affinity, state, dimensions, revision, evidence, updated_at
                  FROM scoped_soul_relationships
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id IN ({placeholders})""",
            (bot_id, session_id, *missing),
        ).fetchall()
        vals = stg.execute(
            f"""SELECT bot_id, session_id, visibility, subject_principal_id, dimension,
                       automatic_value, manual_adjustment, manual_override, effective_value,
                       relationship_revision, evidence, updated_at
                  FROM scoped_soul_relationship_values
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id IN ({placeholders})""",
            (bot_id, session_id, *missing),
        ).fetchall()
        revs = stg.execute(
            f"""SELECT bot_id, session_id, visibility, component, subject_principal_id,
                       revision, updated_at
                  FROM scoped_soul_revisions
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND component='relationship'
                   AND subject_principal_id IN ({placeholders})""",
            (bot_id, session_id, *missing),
        ).fetchall()

        # INSERT OR IGNORE keeps this strictly non-destructive if races occur.
        cur = prod.executemany(
            """INSERT OR IGNORE INTO scoped_soul_relationships(
                   bot_id, session_id, visibility, subject_principal_id,
                   affinity, state, dimensions, revision, evidence, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rels,
        )
        inserted_rel = int(cur.rowcount or 0)
        cur = prod.executemany(
            """INSERT OR IGNORE INTO scoped_soul_relationship_values(
                   bot_id, session_id, visibility, subject_principal_id, dimension,
                   automatic_value, manual_adjustment, manual_override, effective_value,
                   relationship_revision, evidence, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            vals,
        )
        inserted_val = int(cur.rowcount or 0)
        cur = prod.executemany(
            """INSERT OR IGNORE INTO scoped_soul_revisions(
                   bot_id, session_id, visibility, component, subject_principal_id,
                   revision, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            revs,
        )
        inserted_rev = int(cur.rowcount or 0)
        prod.commit()

        after = _subjects(prod, bot_id=bot_id, session_id=session_id)
        plan.update(
            {
                "mode": "apply",
                "backup_path": str(backup_path),
                "inserted_relationships": inserted_rel,
                "inserted_values": inserted_val,
                "inserted_revisions": inserted_rev,
                "production_count_after": len(after),
                "still_missing": sorted(set(missing) - after),
            }
        )
        return plan
    except Exception:
        prod.rollback()
        raise
    finally:
        stg.close()
        prod.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-db", required=True)
    parser.add_argument("--staged-db", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--backup-json", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    production = Path(args.production_db)
    staged = Path(args.staged_db)
    backup = Path(args.backup_json)
    if not production.is_file() or not staged.is_file():
        raise SystemExit("production or staged database missing")

    if args.apply:
        result = apply_fill(
            production,
            staged,
            bot_id=args.bot_id,
            session_id=args.session_id,
            backup_path=backup,
        )
    else:
        result = plan_fill(
            production,
            staged,
            bot_id=args.bot_id,
            session_id=args.session_id,
        )
        # dry-run should not dump full subject list to stdout forever
        result = {k: v for k, v in result.items() if k != "missing_subjects"} | {
            "missing_subjects_count": len(result["missing_subjects"])
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
