#!/usr/bin/env python3
"""Staged multi-scope evidence-summary apply pilot (never touches production DB).

For each selected group Scope:
  1. copy formal+audit slice into a pilot sqlite
  2. apply summaries with affinity/revision guards
  3. verify affinity fingerprint and production remains summary-free

Default scopes: top 2 by formal count from production (read-only source).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.relationship_evidence_summary_dryrun import apply_summaries, plan


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _top_scopes(prod: Path, *, limit: int) -> list[tuple[str, str]]:
    conn = _ro(prod)
    try:
        rows = conn.execute(
            """
            SELECT bot_id, session_id, COUNT(*) AS n
              FROM scoped_soul_relationships
             WHERE visibility='group'
             GROUP BY bot_id, session_id
             ORDER BY n DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [(str(a), str(b)) for a, b, _n in rows]
    finally:
        conn.close()


def _copy_scope_slice(
    prod: Path,
    staged: Path,
    *,
    bot_id: str,
    session_id: str,
) -> dict[str, int]:
    src = _ro(prod)
    staged.parent.mkdir(parents=True, exist_ok=True)
    if staged.exists():
        staged.unlink()
    dst = sqlite3.connect(staged.as_posix())
    try:
        dst.executescript(
            """
            CREATE TABLE scoped_soul_relationships(
                bot_id TEXT, session_id TEXT, visibility TEXT,
                subject_principal_id TEXT, affinity INTEGER, state TEXT,
                dimensions TEXT, revision INTEGER, evidence TEXT, updated_at REAL
            );
            CREATE TABLE scoped_soul_relationship_legacy_events(
                id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
                subject_principal_id TEXT, event_type TEXT, reason TEXT, occurred_at REAL
            );
            """
        )
        formal = src.execute(
            """
            SELECT bot_id, session_id, visibility, subject_principal_id, affinity, state,
                   dimensions, revision, evidence, updated_at
              FROM scoped_soul_relationships
             WHERE bot_id=? AND session_id=? AND visibility='group'
            """,
            (bot_id, session_id),
        ).fetchall()
        dst.executemany(
            "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?,?,?,?,?)",
            formal,
        )
        # slim audit columns for summary builder
        audit = src.execute(
            """
            SELECT id, bot_id, session_id, visibility, subject_principal_id,
                   event_type, COALESCE(reason,''), COALESCE(occurred_at,0)
              FROM scoped_soul_relationship_legacy_events
             WHERE bot_id=? AND session_id=? AND visibility='group'
            """,
            (bot_id, session_id),
        ).fetchall()
        dst.executemany(
            "INSERT INTO scoped_soul_relationship_legacy_events VALUES (?,?,?,?,?,?,?,?)",
            audit,
        )
        dst.commit()
        return {"formal": len(formal), "audit": len(audit)}
    finally:
        src.close()
        dst.close()


def _affinity_fp(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db.as_posix())
    try:
        return {
            str(r[0]): r[1]
            for r in conn.execute(
                "SELECT subject_principal_id, affinity FROM scoped_soul_relationships"
            )
        }
    finally:
        conn.close()


def run_pilot(
    *,
    prod: Path,
    pilot_dir: Path,
    scope_limit: int,
    apply_limit: int,
) -> dict[str, Any]:
    scopes = _top_scopes(prod, limit=scope_limit)
    results: list[dict[str, Any]] = []
    for bot_id, session_id in scopes:
        safe = session_id.replace(":", "_").replace("/", "_")
        staged = pilot_dir / f"slice_{bot_id}_{safe}.sqlite3"
        counts = _copy_scope_slice(prod, staged, bot_id=bot_id, session_id=session_id)
        before = _affinity_fp(staged)
        conn = sqlite3.connect(staged.as_posix())
        try:
            planned = plan(conn, bot_id=bot_id, session_id=session_id, limit=apply_limit)
        finally:
            conn.close()
        applied = apply_summaries(
            staged,
            planned.get("candidates") or [],
            bot_id=bot_id,
            session_id=session_id,
            limit=apply_limit,
        )
        after = _affinity_fp(staged)
        with_summary = 0
        c = sqlite3.connect(staged.as_posix())
        try:
            with_summary = int(
                c.execute(
                    """
                    SELECT COUNT(*) FROM scoped_soul_relationships
                     WHERE evidence LIKE '%historical_audit_summary%'
                    """
                ).fetchone()[0]
            )
        finally:
            c.close()
        results.append(
            {
                "bot_id": bot_id,
                "session_id": session_id,
                "staged": str(staged),
                "slice": counts,
                "apply": applied,
                "affinity_unchanged": before == after,
                "with_summary": with_summary,
                "formal_fingerprint_n": len(after),
            }
        )

    # production still free of summaries
    pc = _ro(prod)
    try:
        prod_summary = int(
            pc.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
    finally:
        pc.close()

    ok = all(r.get("affinity_unchanged") for r in results) and prod_summary == 0
    return {
        "mode": "multi-scope-staged-pilot",
        "generated_at": time.time(),
        "prod": str(prod),
        "scope_limit": scope_limit,
        "apply_limit": apply_limit,
        "scopes_run": len(results),
        "results": results,
        "prod_evidence_summary_rows": prod_summary,
        "ok": ok,
        "writes_production": False,
        "phase2_promote_allowed": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument(
        "--pilot-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "relationship_evidence_multi_scope_pilot"
        ),
    )
    p.add_argument("--scope-limit", type=int, default=2)
    p.add_argument("--apply-limit", type=int, default=30)
    p.add_argument("--report", default="")
    args = p.parse_args()
    report = run_pilot(
        prod=Path(args.prod_db),
        pilot_dir=Path(args.pilot_dir),
        scope_limit=int(args.scope_limit),
        apply_limit=int(args.apply_limit),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    return 0 if report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
