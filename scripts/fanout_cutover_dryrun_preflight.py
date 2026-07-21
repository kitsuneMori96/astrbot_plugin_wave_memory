#!/usr/bin/env python3
"""Dry-run preflight for fanout physical cleanup cutover.

Never switches production files. Emits a machine-checkable readiness report:
hard gates, disk, WAL/shm, index assets, and ordered ops with rollback names.

Live apply is intentionally NOT implemented.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path

from scripts.fanout_cutover_runbook import build_plan


def _wal_info(db: Path) -> dict:
    wal = db.parent / f"{db.name}-wal"
    shm = db.parent / f"{db.name}-shm"
    info = {
        "wal_exists": wal.exists(),
        "shm_exists": shm.exists(),
        "wal_size": wal.stat().st_size if wal.exists() else 0,
        "shm_size": shm.stat().st_size if shm.exists() else 0,
    }
    # Readonly PRAGMA where possible
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30)
        try:
            jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            info["journal_mode"] = str(jm)
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover
        info["journal_mode_error"] = str(exc)[:160]
    return info


def preflight(
    *,
    prod: Path,
    vacuumed: Path,
    index_dir: Path,
    data_dir: Path | None = None,
) -> dict:
    data_dir = data_dir or prod.parent
    plan = build_plan(prod=prod, vacuumed=vacuumed, index_dir=index_dir)
    usage = shutil.disk_usage(str(data_dir))
    ts = int(time.time())
    prod_size = prod.stat().st_size if prod.exists() else 0
    vac_size = vacuumed.stat().st_size if vacuumed.exists() else 0
    wal = _wal_info(prod)
    idx_files = sorted(p.name for p in index_dir.glob("memory.hnsw*")) if index_dir.is_dir() else []

    # Rename-based rollback is preferred (no full copy). Still require free space
    # for temporary peak + index install.
    free_ok = usage.free > max(vac_size, 512 * 1024 * 1024)
    copy_rollback_ok = usage.free > prod_size * 1.1

    blockers: list[str] = []
    if not plan.get("package_safe_for_cutover"):
        blockers.append("package_not_safe_for_cutover")
    if plan.get("needs_refresh_before_cutover"):
        blockers.append("package_needs_refresh")
    if not free_ok:
        blockers.append("insufficient_disk_free")
    if not idx_files:
        blockers.append("missing_memory_hnsw_assets")
    if wal.get("wal_size", 0) > 64 * 1024 * 1024:
        blockers.append("prod_wal_large_checkpoint_required_before_swap")

    ordered_ops = [
        "MAINT: stop/drain AstrBot writers (plugin unload or process stop)",
        "CHECKPOINT: open prod DB read-write and run WAL checkpoint TRUNCATE (after writers stopped)",
        "VERIFY: re-run fanout_cutover_runbook.py; require package_safe_for_cutover=true",
        f"BACKUP-RENAME: mv wave_memory.db -> wave_memory.pre_cutover_{ts}.db (and -wal/-shm if remain)",
        f"BACKUP-INDEX: move current memory.hnsw* aside to memory.hnsw.pre_cutover_{ts}/",
        "INSTALL-DB: copy/move vacuumed cleaned DB into place as wave_memory.db",
        "INSTALL-INDEX: install staged indexes/memory.hnsw* into data_dir",
        "START: start AstrBot / reload plugin",
        "SMOKE: person_search + affinity historical_audit + FTS + formal counts",
        "ROLLBACK: restore pre_cutover DB/index names if smoke fails",
    ]

    return {
        "mode": "dry-run-preflight",
        "generated_at": time.time(),
        "apply_implemented": False,
        "phase2_promote_allowed": False,
        "package_safe_for_cutover": plan.get("package_safe_for_cutover"),
        "needs_refresh_before_cutover": plan.get("needs_refresh_before_cutover"),
        "hard_gates": plan.get("hard_gates"),
        "drift": plan.get("drift"),
        "disk": {
            "free_bytes": usage.free,
            "free_gb": round(usage.free / 1024**3, 2),
            "prod_size_gb": round(prod_size / 1024**3, 2),
            "vac_size_gb": round(vac_size / 1024**3, 2),
            "free_ok_for_rename_install": free_ok,
            "free_ok_for_full_copy_rollback": copy_rollback_ok,
        },
        "prod_wal": wal,
        "assets": {
            "prod_db": str(prod),
            "vacuumed_db": str(vacuumed),
            "index_dir": str(index_dir),
            "index_files": idx_files,
        },
        "proposed_rollback_names": {
            "db": f"wave_memory.pre_cutover_{ts}.db",
            "index_dir": f"memory.hnsw.pre_cutover_{ts}",
        },
        "blockers_for_live_cutover": blockers
        + [
            "user_explicit_cutover_authorization",
            "maintenance_window",
        ],
        "ordered_ops": ordered_ops,
        "ready_technically_except_auth": (
            plan.get("package_safe_for_cutover") is True
            and not plan.get("needs_refresh_before_cutover")
            and free_ok
            and bool(idx_files)
        ),
        "note": "Large WAL means live swap without checkpoint risks inconsistent open; auth still required.",
    }


def main() -> int:
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
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    report = preflight(
        prod=Path(args.prod_db),
        vacuumed=Path(args.vacuumed_db),
        index_dir=Path(args.index_dir),
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
