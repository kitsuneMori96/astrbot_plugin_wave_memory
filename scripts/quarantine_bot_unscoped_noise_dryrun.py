#!/usr/bin/env python3
"""Dry-run / staged quarantine for unscoped bot noise memories.

Targets residual unscoped rows that are bot/system senders and should not
pollute peer formalize or ranking. Uses memories.quarantine=1 which
memory_repo already filters out of normal recall.

Default: read-only plan. Production apply requires confirmation + flags.
Never Phase2 promote. Never fanout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

CONFIRMATION = "quarantine-bot-unscoped-noise"
NOISE_SENDERS = {"bot", "bot_remember"}
RESOLUTION = "noise_bot_unscoped_quarantine"


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _is_prod_like(path: Path) -> bool:
    p = path.as_posix()
    return path.name == "wave_memory.db" and "plugin_data" in p and "backups" not in p


def _is_noise_sender(sender_id: str | None, sender_name: str | None) -> bool:
    sid = str(sender_id or "").strip()
    sname = str(sender_name or "")
    if sid in NOISE_SENDERS:
        return True
    if sid.startswith("["):
        return True
    if "经历" in sname:
        return True
    return False


def plan(conn: sqlite3.Connection, *, limit: int = 0) -> dict[str, Any]:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
    if "quarantine" not in cols:
        return {
            "mode": "dry-run",
            "error": "no_quarantine_column",
            "candidates": [],
            "candidate_count": 0,
        }
    rows = conn.execute(
        """
        SELECT id, group_id, sender_id, sender_name, COALESCE(quarantine, 0),
               COALESCE(resolution_state, ''), COALESCE(importance, 0),
               substr(COALESCE(content, ''), 1, 50)
          FROM memories
         WHERE COALESCE(bot_id, '') = '' OR COALESCE(session_id, '') = ''
        """
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    skip = Counter()
    for mid, gid, sid, sname, q, res, imp, content in rows:
        if int(q or 0) == 1:
            skip["already_quarantined"] += 1
            continue
        if res == RESOLUTION:
            skip["already_marked"] += 1
            continue
        if not _is_noise_sender(sid, sname):
            skip["not_noise_sender"] += 1
            continue
        candidates.append(
            {
                "id": int(mid),
                "group_id": gid,
                "sender_id": sid,
                "sender_name": sname,
                "quarantine_before": int(q or 0),
                "resolution_before": res,
                "importance": imp,
                "content_prefix": content,
                "proposed": {
                    "quarantine": 1,
                    "resolution_state": RESOLUTION,
                },
            }
        )
        if limit and len(candidates) >= int(limit):
            break
    by_sender = Counter(str(c.get("sender_id") or "") for c in candidates)
    by_group = Counter(str(c.get("group_id") or "") for c in candidates)
    return {
        "mode": "dry-run",
        "generated_at": time.time(),
        "scanned_unscoped": len(rows),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "by_sender": dict(by_sender.most_common(20)),
        "by_group_top": dict(by_group.most_common(15)),
        "skip_reasons": dict(skip),
        "effect": "memory_repo filters COALESCE(quarantine,0)=0 from normal recall",
        "confirmation_for_apply": CONFIRMATION,
        "writes_production": False,
        "phase2_promote_allowed": False,
        "rules": {
            "no_fanout": True,
            "no_delete": True,
            "no_scope_guess": True,
            "update_only": True,
        },
    }


def apply_quarantine(target: Path, candidates: list[dict[str, Any]], *, limit: int = 0) -> dict[str, Any]:
    conn = sqlite3.connect(target.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    batch = candidates if not limit else candidates[: int(limit)]
    updated = skipped = mismatch = 0
    try:
        before_q = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE COALESCE(quarantine,0)=1"
            ).fetchone()[0]
        )
        for c in batch:
            mid = int(c["id"])
            row = conn.execute(
                """
                SELECT COALESCE(quarantine,0), COALESCE(resolution_state,''),
                       COALESCE(bot_id,''), COALESCE(session_id,'')
                  FROM memories WHERE id=?
                """,
                (mid,),
            ).fetchone()
            if not row:
                skipped += 1
                continue
            q, res, bot_id, session_id = row
            if int(q or 0) == 1:
                skipped += 1
                continue
            # only unscoped
            if bot_id and session_id:
                mismatch += 1
                continue
            cur = conn.execute(
                """
                UPDATE memories
                   SET quarantine=1,
                       resolution_state=?
                 WHERE id=?
                   AND COALESCE(quarantine,0)=0
                   AND (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
                """,
                (RESOLUTION, mid),
            )
            if cur.rowcount != 1:
                mismatch += 1
                conn.rollback()
                continue
            conn.commit()
            updated += 1
        after_q = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE COALESCE(quarantine,0)=1"
            ).fetchone()[0]
        )
        return {
            "applied": True,
            "updated": updated,
            "skipped": skipped,
            "mismatch": mismatch,
            "batch": len(batch),
            "quarantine_before": before_q,
            "quarantine_after": after_q,
            "phase2_promote_allowed": False,
            "writes_new_rows": False,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--report", default="")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--apply-db", default="")
    p.add_argument("--confirmation", default="")
    p.add_argument("--writers-stopped", action="store_true")
    p.add_argument("--allow-prod-apply", action="store_true")
    p.add_argument("--auto-staged", action="store_true")
    p.add_argument(
        "--staged-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "bot_unscoped_noise_quarantine"
        ),
    )
    args = p.parse_args(argv)

    source = Path(args.db)
    conn = _ro(source)
    try:
        report = plan(conn, limit=int(args.limit) if not args.apply else 0)
        if args.apply and args.limit:
            # re-plan with limit for apply batch size control
            report = plan(conn, limit=max(int(args.limit), 0) or 0)
    finally:
        conn.close()

    out = {k: v for k, v in report.items() if k != "candidates"}
    out["sample"] = (report.get("candidates") or [])[:10]

    def _emit(payload: dict[str, Any], code: int = 0) -> int:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        if args.report:
            rp = Path(args.report)
            rp.parent.mkdir(parents=True, exist_ok=True)
            # include candidates only if small
            dump = dict(payload)
            if len(report.get("candidates") or []) <= 200:
                dump["candidates"] = report.get("candidates")
            rp.write_text(json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return code

    if not args.apply:
        return _emit(out, 0)

    cands = report.get("candidates") or []
    if args.confirmation != CONFIRMATION:
        out["apply_error"] = f"need --confirmation {CONFIRMATION}"
        out["applied"] = False
        return _emit(out, 2)

    if args.auto_staged and not args.apply_db:
        staged_dir = Path(args.staged_dir)
        staged_dir.mkdir(parents=True, exist_ok=True)
        dest = staged_dir / "bot_noise_quarantine_pilot.sqlite3"
        if dest.exists():
            dest.unlink()
        src = _ro(source)
        dst = sqlite3.connect(dest.as_posix())
        try:
            cols = [r[1] for r in src.execute("PRAGMA table_info(memories)")]
            col_list = ", ".join(cols)
            dst.execute("ATTACH DATABASE ? AS prod", (source.as_posix(),))
            dst.execute(f"CREATE TABLE memories AS SELECT {col_list} FROM prod.memories WHERE 0")
            ids = [int(c["id"]) for c in cands]
            if ids:
                # chunk insert
                chunk = 500
                for i in range(0, len(ids), chunk):
                    part = ids[i : i + chunk]
                    ph = ",".join("?" * len(part))
                    dst.execute(
                        f"INSERT INTO memories SELECT {col_list} FROM prod.memories WHERE id IN ({ph})",
                        part,
                    )
            dst.commit()
            out["staged_copy"] = {"path": str(dest), "copied": len(ids)}
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

    result = apply_quarantine(target, cands, limit=int(args.limit) or 0)
    out["mode"] = "apply"
    out["apply"] = result
    out["target_db"] = str(target)
    out["applied"] = bool(result.get("applied"))
    out["ok"] = bool(result.get("updated", 0) > 0)
    return _emit(out, 0 if out.get("ok") else 2)


if __name__ == "__main__":
    raise SystemExit(main())
