#!/usr/bin/env python3
"""Point-fix refill: formal rows that have audit but lack historical_audit_summary.

Default is dry-run / refuse production apply.
Does not change affinity/revision. Does not fanout. Does not Phase2 promote.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.relationship_evidence_summary_dryrun import (
    _build_summary,
    _merge_evidence,
    _ro,
)


CONFIRMATION = "refill-missing-evidence-summaries"


def _is_prod_like(path: Path) -> bool:
    p = path.as_posix()
    return path.name == "wave_memory.db" and "plugin_data" in p and "backups" not in p


def find_missing(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "scoped_soul_relationship_legacy_events" not in tables:
        return []
    rows = conn.execute(
        """
        SELECT r.bot_id, r.session_id, r.subject_principal_id, r.affinity, r.revision, r.evidence
          FROM scoped_soul_relationships r
         WHERE (r.evidence IS NULL OR r.evidence NOT LIKE '%historical_audit_summary%')
           AND EXISTS (
             SELECT 1 FROM scoped_soul_relationship_legacy_events e
              WHERE e.bot_id=r.bot_id AND e.session_id=r.session_id
                AND e.visibility=r.visibility
                AND e.subject_principal_id=r.subject_principal_id
           )
         ORDER BY r.bot_id, r.session_id, r.subject_principal_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for bot_id, session_id, subject, affinity, revision, evidence in rows:
        audit_total = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                """,
                (bot_id, session_id, subject),
            ).fetchone()[0]
        )
        type_counts = [
            (str(r[0]), int(r[1]))
            for r in conn.execute(
                """
                SELECT event_type, COUNT(*) AS n
                  FROM scoped_soul_relationship_legacy_events
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                 GROUP BY event_type
                 ORDER BY n DESC
                 LIMIT 5
                """,
                (bot_id, session_id, subject),
            ).fetchall()
        ]
        rr = conn.execute(
            """
            SELECT COALESCE(reason, '') FROM scoped_soul_relationship_legacy_events
             WHERE bot_id=? AND session_id=? AND visibility='group'
               AND subject_principal_id=?
             ORDER BY occurred_at DESC, id DESC LIMIT 1
            """,
            (bot_id, session_id, subject),
        ).fetchone()
        recent_reason = str(rr[0] if rr else "")
        summary = _build_summary(type_counts, audit_total, recent_reason)
        out.append(
            {
                "bot_id": bot_id,
                "session_id": session_id,
                "subject_principal_id": subject,
                "affinity": affinity,
                "revision": revision,
                "audit_total": audit_total,
                "evidence_before_prefix": (str(evidence or ""))[:100],
                "proposed_evidence_append": {
                    "kind": "historical_audit_summary",
                    "summary": summary,
                    "affects_affinity": False,
                },
            }
        )
    return out


def apply_missing(target: Path, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    conn = sqlite3.connect(target.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    updated = skipped = mismatch = 0
    try:
        before_n = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
        for c in candidates:
            bot_id = str(c["bot_id"])
            session_id = str(c["session_id"])
            subject = str(c["subject_principal_id"])
            expected_aff = c.get("affinity")
            expected_rev = c.get("revision")
            row = conn.execute(
                """
                SELECT affinity, revision, evidence
                  FROM scoped_soul_relationships
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                """,
                (bot_id, session_id, subject),
            ).fetchone()
            if not row:
                skipped += 1
                continue
            aff, rev, evidence = row
            if aff != expected_aff or rev != expected_rev:
                mismatch += 1
                continue
            append = c.get("proposed_evidence_append") or {}
            if not append.get("summary"):
                skipped += 1
                continue
            merged = _merge_evidence(evidence if isinstance(evidence, str) else None, append)
            if merged is None:
                skipped += 1
                continue
            cur = conn.execute(
                """
                UPDATE scoped_soul_relationships
                   SET evidence=?
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                   AND affinity IS ?
                   AND revision IS ?
                """,
                (merged, bot_id, session_id, subject, expected_aff, expected_rev),
            )
            if cur.rowcount != 1:
                mismatch += 1
                conn.rollback()
                continue
            after = conn.execute(
                """
                SELECT affinity, revision FROM scoped_soul_relationships
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                """,
                (bot_id, session_id, subject),
            ).fetchone()
            if not after or after[0] != expected_aff or after[1] != expected_rev:
                conn.rollback()
                mismatch += 1
                continue
            conn.commit()
            updated += 1
        after_n = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scoped_soul_relationships
                 WHERE evidence LIKE '%historical_audit_summary%'
                """
            ).fetchone()[0]
        )
        return {
            "applied": True,
            "updated": updated,
            "skipped": skipped,
            "affinity_mismatch": mismatch,
            "summaries_before": before_n,
            "summaries_after": after_n,
            "writes_affinity": False,
            "phase2_promote_allowed": False,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument("--report", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-db", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--allow-prod-apply", action="store_true")
    parser.add_argument(
        "--auto-staged",
        action="store_true",
        help="copy only missing rows+audit into staged db and apply there",
    )
    parser.add_argument(
        "--staged-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "evidence_summary_refill_staged"
        ),
    )
    args = parser.parse_args(argv)

    source = Path(args.db)
    conn = _ro(source)
    try:
        candidates = find_missing(conn)
    finally:
        conn.close()

    out: dict[str, Any] = {
        "mode": "dry-run",
        "generated_at": time.time(),
        "missing_n": len(candidates),
        "candidates": candidates,
        "confirmation_for_apply": CONFIRMATION,
        "writes_production": False,
        "phase2_promote_allowed": False,
        "writes_affinity": False,
    }

    def _emit(payload: dict[str, Any], code: int = 0) -> int:
        # compact print without dumping huge fields if any
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(text + "\n", encoding="utf-8")
        return code

    if not args.apply:
        return _emit(out, 0)

    if args.confirmation != CONFIRMATION:
        out["apply_error"] = f"need --confirmation {CONFIRMATION}"
        out["applied"] = False
        return _emit(out, 2)

    if args.auto_staged and not args.apply_db:
        staged_dir = Path(args.staged_dir)
        staged_dir.mkdir(parents=True, exist_ok=True)
        dest = staged_dir / "missing_refill_pilot.sqlite3"
        if dest.exists():
            dest.unlink()
        src = _ro(source)
        dst = sqlite3.connect(dest.as_posix())
        try:
            dst.execute("ATTACH DATABASE ? AS prod", (source.as_posix(),))
            for table in (
                "scoped_soul_relationships",
                "scoped_soul_relationship_legacy_events",
            ):
                cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                col_list = ", ".join(cols)
                dst.execute(
                    f"CREATE TABLE {table} AS SELECT {col_list} FROM prod.{table} WHERE 0"
                )
            for c in candidates:
                dst.execute(
                    """
                    INSERT INTO scoped_soul_relationships
                    SELECT * FROM prod.scoped_soul_relationships
                     WHERE bot_id=? AND session_id=? AND visibility='group'
                       AND subject_principal_id=?
                    """,
                    (c["bot_id"], c["session_id"], c["subject_principal_id"]),
                )
                dst.execute(
                    """
                    INSERT INTO scoped_soul_relationship_legacy_events
                    SELECT * FROM prod.scoped_soul_relationship_legacy_events
                     WHERE bot_id=? AND session_id=? AND visibility='group'
                       AND subject_principal_id=?
                    """,
                    (c["bot_id"], c["session_id"], c["subject_principal_id"]),
                )
            dst.commit()
            out["staged_copy"] = {"path": str(dest), "rows": len(candidates)}
            target = dest
        finally:
            src.close()
            dst.close()
    else:
        target = Path(args.apply_db) if args.apply_db else source
        if _is_prod_like(target) and not args.allow_prod_apply:
            out["apply_error"] = (
                "refuse_prod_apply: use --auto-staged or --apply-db staged "
                "or --allow-prod-apply"
            )
            out["applied"] = False
            return _emit(out, 2)
        if _is_prod_like(target) and not args.writers_stopped:
            out["apply_error"] = "writers_stopped_required_for_prod"
            out["applied"] = False
            return _emit(out, 2)

    result = apply_missing(target, candidates)
    out["mode"] = "apply"
    out["apply"] = result
    out["target_db"] = str(target)
    out["applied"] = bool(result.get("applied"))
    out["ok"] = bool(result.get("updated", 0) == len(candidates) and result.get("updated", 0) > 0)
    return _emit(out, 0 if out.get("ok") else 2)


if __name__ == "__main__":
    raise SystemExit(main())
