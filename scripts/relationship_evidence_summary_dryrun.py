#!/usr/bin/env python3
"""Dry-run: propose readable evidence summaries for formal relationships.

Read-only against production by default. Never updates affinity / values /
revision / evidence unless --apply (which still refuses prod without override).

Summary is derived from scoped_soul_relationship_legacy_events (historical audit)
and does not replay scores.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _machine_evidence(raw: str | None) -> bool:
    if raw is None or not str(raw).strip():
        return True
    try:
        data = json.loads(raw)
    except Exception:
        return False
    if not isinstance(data, list):
        return False
    if not data:
        return True
    # machine-style: list of dicts with relationship_event_id / value_layer only
    for item in data:
        if not isinstance(item, dict):
            return False
        keys = set(item.keys())
        if keys <= {"relationship_event_id", "dimension", "value_layer", "source"}:
            continue
        if "summary" in keys or "text" in keys or "narrative" in keys:
            return False
    return True


def _build_summary(event_types: list[tuple[str, int]], total: int, recent_reason: str) -> str:
    if total <= 0:
        return ""
    parts = [f"历史审计事件 {total} 条"]
    if event_types:
        top = "、".join(f"{et}×{n}" for et, n in event_types[:3])
        parts.append(f"类型：{top}")
    if recent_reason:
        rr = recent_reason.strip().replace("\n", " ")
        if len(rr) > 40:
            rr = rr[:40] + "…"
        parts.append(f"近因：{rr}")
    parts.append("（只读摘要，不影响亲和分）")
    return "；".join(parts)


def plan(
    conn: sqlite3.Connection,
    *,
    bot_id: str = "yushu",
    session_id: str = "羽书:group:398291136",
    limit: int = 50,
) -> dict[str, Any]:
    tables = {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    has_audit = "scoped_soul_relationship_legacy_events" in tables
    rows = conn.execute(
        """
        SELECT subject_principal_id, affinity, state, evidence, revision
          FROM scoped_soul_relationships
         WHERE bot_id=? AND session_id=? AND visibility='group'
         ORDER BY affinity DESC
        """,
        (bot_id, session_id),
    ).fetchall()

    machine = 0
    with_audit = 0
    candidates: list[dict[str, Any]] = []
    affinity_fingerprint_before: list[tuple[str, int | None]] = []

    for subject, affinity, state, evidence, revision in rows:
        affinity_fingerprint_before.append((str(subject), int(affinity) if affinity is not None else None))
        if not _machine_evidence(evidence if isinstance(evidence, str) else None):
            continue
        machine += 1
        audit_total = 0
        type_counts: list[tuple[str, int]] = []
        recent_reason = ""
        if has_audit:
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
            if audit_total:
                with_audit += 1
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
        if audit_total <= 0:
            continue
        summary = _build_summary(type_counts, audit_total, recent_reason)
        candidates.append(
            {
                "subject_principal_id": subject,
                "affinity": affinity,
                "state": state,
                "revision": revision,
                "audit_total": audit_total,
                "proposed_evidence_append": {
                    "kind": "historical_audit_summary",
                    "summary": summary,
                    "affects_affinity": False,
                },
                "evidence_before_is_machine": True,
            }
        )

    candidates.sort(key=lambda c: int(c.get("audit_total") or 0), reverse=True)
    sample = candidates[: max(0, int(limit))]

    return {
        "mode": "dry-run",
        "generated_at": time.time(),
        "scope": {"bot_id": bot_id, "session_id": session_id, "visibility": "group"},
        "formal_rows": len(rows),
        "machine_evidence_rows": machine,
        "machine_with_audit": with_audit,
        "summary_candidates": len(candidates),
        "has_audit_table": has_audit,
        "affinity_unchanged_guarantee": True,
        "affinity_fingerprint_sha_note": "apply must re-check affinity per subject before write",
        "affinity_sample_before": affinity_fingerprint_before[:5],
        "sample": sample,
        "candidates": candidates,
        "writes_affinity": False,
        "phase2_promote_allowed": False,
    }


def _merge_evidence(raw: str | None, append: dict[str, Any]) -> str | None:
    """Append summary object if not already present; return None if no change."""
    data: list[Any]
    if raw is None or not str(raw).strip():
        data = []
    else:
        try:
            loaded = json.loads(raw)
        except Exception:
            return None
        if not isinstance(loaded, list):
            return None
        data = list(loaded)
    for item in data:
        if isinstance(item, dict) and item.get("kind") == "historical_audit_summary":
            return None  # already applied
    if not _machine_evidence(raw if isinstance(raw, str) else None) and data:
        # only append onto machine lists or empty
        if not all(isinstance(x, dict) for x in data):
            return None
    data.append(dict(append))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def apply_summaries(
    target_db: Path,
    candidates: list[dict[str, Any]],
    *,
    bot_id: str,
    session_id: str,
    limit: int = 0,
) -> dict[str, Any]:
    """Update evidence only. Aborts a row if affinity/state/revision would drift."""
    conn = sqlite3.connect(target_db.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    updated = skipped = affinity_mismatch = 0
    batch = candidates if not limit else candidates[: int(limit)]
    try:
        for c in batch:
            subject = str(c["subject_principal_id"])
            expected_aff = c.get("affinity")
            expected_rev = c.get("revision")
            row = conn.execute(
                """
                SELECT affinity, state, revision, evidence
                  FROM scoped_soul_relationships
                 WHERE bot_id=? AND session_id=? AND visibility='group'
                   AND subject_principal_id=?
                """,
                (bot_id, session_id, subject),
            ).fetchone()
            if not row:
                skipped += 1
                continue
            aff, state, rev, evidence = row
            if aff != expected_aff or rev != expected_rev:
                affinity_mismatch += 1
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
                affinity_mismatch += 1
                conn.rollback()
                continue
            # post-check affinity unchanged
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
                affinity_mismatch += 1
                continue
            conn.commit()
            updated += 1
        return {
            "applied": True,
            "updated": updated,
            "skipped": skipped,
            "affinity_mismatch": affinity_mismatch,
            "batch": len(batch),
            "writes_affinity": False,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
        help="source DB for planning (read-only unless apply-db omitted carefully)",
    )
    parser.add_argument("--bot-id", default="yushu")
    parser.add_argument("--session-id", default="羽书:group:398291136")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--report", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--apply-db",
        default="",
        help="target DB for evidence updates (use staged copy; default refuse prod)",
    )
    parser.add_argument("--apply-limit", type=int, default=0)
    parser.add_argument(
        "--allow-prod-apply",
        action="store_true",
        help="allow writing plugin_data wave_memory.db (dangerous)",
    )
    args = parser.parse_args()
    source = Path(args.db)
    conn = _ro(source)
    try:
        report = plan(
            conn,
            bot_id=args.bot_id,
            session_id=args.session_id,
            limit=int(args.limit) if not args.apply else max(int(args.limit), int(args.apply_limit) or int(args.limit)),
        )
    finally:
        conn.close()

    out = {k: v for k, v in report.items() if k != "candidates"}
    if args.apply:
        target = Path(args.apply_db) if args.apply_db else source
        prod_like = target.name == "wave_memory.db" and "plugin_data" in target.as_posix()
        if prod_like and not args.allow_prod_apply:
            out["apply_error"] = "refuse_prod_apply: use --apply-db staged path or --allow-prod-apply"
            out["applied"] = False
            text = json.dumps(out, ensure_ascii=False, indent=2)
            print(text)
            if args.report:
                Path(args.report).parent.mkdir(parents=True, exist_ok=True)
                Path(args.report).write_text(text, encoding="utf-8")
            return 2
        result = apply_summaries(
            target,
            report.get("candidates") or [],
            bot_id=args.bot_id,
            session_id=args.session_id,
            limit=int(args.apply_limit) or int(args.limit),
        )
        out["apply"] = result
        out["mode"] = "apply"
        out["target_db"] = str(target)

    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
