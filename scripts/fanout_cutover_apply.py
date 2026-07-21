#!/usr/bin/env python3
"""Apply (or dry-run) fanout physical cleanup cutover for production.

Default is dry-run and never mutates production files.

Live switch requires ALL of:
  --apply
  --confirmation cutover-fanout-cleaned-db
  --writers-stopped
  package hard gates green
  WAL small enough OR --checkpoint after writers stopped

This does NOT re-open Phase-2 fanout promote.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from scripts.fanout_cutover_dryrun_preflight import preflight
from scripts.fanout_cutover_package_accept import accept

CONFIRMATION = "cutover-fanout-cleaned-db"
WAL_SOFT_LIMIT = 64 * 1024 * 1024


class CutoverError(RuntimeError):
    """Raised when cutover cannot proceed safely."""


def _checkpoint_truncate(db: Path) -> dict:
    conn = sqlite3.connect(db.as_posix(), timeout=600)
    try:
        conn.execute("PRAGMA busy_timeout=600000")
        before = (db.parent / f"{db.name}-wal")
        before_size = before.stat().st_size if before.exists() else 0
        # TRUNCATE requires no other writers holding the WAL.
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        after_size = before.stat().st_size if before.exists() else 0
        return {
            "checkpoint_row": list(row) if row else None,
            "wal_before": before_size,
            "wal_after": after_size,
        }
    finally:
        conn.close()


def _move_aside(path: Path, dest: Path) -> None:
    if not path.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise CutoverError(f"rollback_target_exists:{dest}")
    path.rename(dest)


def _post_checks(prod: Path, *, expected_audit: int, expected_formal: int) -> dict:
    conn = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True, timeout=120)
    try:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        marked = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
        formal = int(conn.execute("SELECT COUNT(*) FROM scoped_soul_relationships").fetchone()[0])
        audit = (
            int(
                conn.execute(
                    "SELECT COUNT(*) FROM scoped_soul_relationship_legacy_events"
                ).fetchone()[0]
            )
            if "scoped_soul_relationship_legacy_events" in tables
            else -1
        )
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
        ok = (
            quick == "ok"
            and marked == 0
            and formal == expected_formal
            and audit == expected_audit
        )
        return {
            "ok": ok,
            "quick_check": quick,
            "marked": marked,
            "formal": formal,
            "audit": audit,
            "expected_formal": expected_formal,
            "expected_audit": expected_audit,
        }
    finally:
        conn.close()


def apply_cutover(
    *,
    prod: Path,
    vacuumed: Path,
    index_dir: Path,
    confirmation: str,
    writers_stopped: bool,
    do_checkpoint: bool,
    dry_run: bool,
) -> dict:
    report = preflight(prod=prod, vacuumed=vacuumed, index_dir=index_dir)
    report["mode"] = "dry-run-apply" if dry_run else "apply"
    report["confirmation_provided"] = confirmation == CONFIRMATION
    report["writers_stopped_flag"] = bool(writers_stopped)
    report["checkpoint_requested"] = bool(do_checkpoint)

    if dry_run:
        report["actions"] = ["preflight_only"]
        report["switched"] = False
        return report

    if confirmation != CONFIRMATION:
        raise CutoverError(f"confirmation_required:{CONFIRMATION}")
    if not writers_stopped:
        raise CutoverError("writers_stopped_required")
    if not report.get("package_safe_for_cutover"):
        raise CutoverError("package_not_safe_for_cutover")
    if report.get("needs_refresh_before_cutover"):
        raise CutoverError("package_needs_refresh")

    # Package accept with prod compare (extra gate)
    acc = accept(
        vacuumed,
        index_dir / "memory.hnsw",
        dimension=1024,
        prod_db=prod,
    )
    report["package_accept"] = {
        "passed": acc.get("passed"),
        "hard_gates": acc.get("hard_gates"),
        "audit_rows": (acc.get("checks") or {}).get("audit_rows"),
        "formal": (acc.get("checks") or {}).get("formal"),
    }
    if not acc.get("passed"):
        raise CutoverError("package_accept_failed")

    wal_size = int((report.get("prod_wal") or {}).get("wal_size") or 0)
    actions: list[str] = []
    if wal_size > WAL_SOFT_LIMIT:
        if not do_checkpoint:
            raise CutoverError("prod_wal_large_pass_checkpoint_flag")
        ck = _checkpoint_truncate(prod)
        actions.append(f"wal_checkpoint_truncate:{ck}")
        report["checkpoint"] = ck
        # Re-read wal size
        wal_path = prod.parent / f"{prod.name}-wal"
        wal_size = wal_path.stat().st_size if wal_path.exists() else 0
        if wal_size > WAL_SOFT_LIMIT:
            raise CutoverError(f"wal_still_large_after_checkpoint:{wal_size}")

    # Prefer vacuumed package counts from accept checks (package is source of truth after swap).
    expected_audit = int((acc.get("checks") or {}).get("audit_rows") or 0)
    expected_formal = int((acc.get("checks") or {}).get("formal") or 0)

    ts = int(time.time())
    data_dir = prod.parent
    rb_db = data_dir / f"wave_memory.pre_cutover_{ts}.db"
    rb_idx = data_dir / f"memory.hnsw.pre_cutover_{ts}"
    rb_idx.mkdir(parents=True, exist_ok=False)

    # Move production DB (+ sidecars) aside
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(prod) + suffix) if suffix else prod
        if src.exists():
            dest = Path(str(rb_db) + suffix) if suffix else rb_db
            src.rename(dest)
            actions.append(f"rename:{src.name}->{dest.name}")

    # Install vacuumed DB as production name
    shutil.copy2(vacuumed, prod)
    actions.append(f"install_db:{vacuumed.name}->{prod.name}")

    # Move current memory.hnsw* files aside (skip dirs like pre_cutover/failed_cutover).
    for p in sorted(data_dir.glob("memory.hnsw*")):
        if p.is_dir():
            continue
        # Never move the rollback directory we just created into itself.
        if p.resolve() == rb_idx.resolve():
            continue
        dest = rb_idx / p.name
        if dest.exists():
            raise CutoverError(f"rollback_index_exists:{dest}")
        p.rename(dest)
        actions.append(f"rename_index:{p.name}->{dest}")
    for p in sorted(index_dir.glob("memory.hnsw*")):
        if p.is_dir():
            continue
        dest = data_dir / p.name
        if dest.exists():
            raise CutoverError(f"index_dest_exists:{dest}")
        shutil.copy2(p, dest)
        actions.append(f"install_index:{p.name}")

    post = _post_checks(prod, expected_audit=expected_audit, expected_formal=expected_formal)
    report["post_checks"] = post
    report["actions"] = actions
    report["rollback"] = {
        "db": str(rb_db),
        "index_dir": str(rb_idx),
    }
    report["switched"] = True
    if not post.get("ok"):
        raise CutoverError(f"post_checks_failed:{post}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument(
        "--vacuumed-db",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3"
        ),
    )
    parser.add_argument(
        "--index-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "fanout_cleanup_full_staged/indexes"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="perform live cutover")
    parser.add_argument(
        "--confirmation",
        default="",
        help=f"required for --apply: {CONFIRMATION}",
    )
    parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="operator affirms AstrBot writers are stopped/drained",
    )
    parser.add_argument(
        "--checkpoint",
        action="store_true",
        help="run PRAGMA wal_checkpoint(TRUNCATE) before swap (requires writers stopped)",
    )
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    dry_run = not args.apply
    try:
        report = apply_cutover(
            prod=Path(args.prod_db),
            vacuumed=Path(args.vacuumed_db),
            index_dir=Path(args.index_dir),
            confirmation=args.confirmation,
            writers_stopped=bool(args.writers_stopped),
            do_checkpoint=bool(args.checkpoint),
            dry_run=dry_run,
        )
    except CutoverError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "apply_implemented": True,
            "switched": False,
            "phase2_promote_allowed": False,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    report["ok"] = True
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
