#!/usr/bin/env python3
"""Rollback a fanout physical-cleanup cutover.

Default is dry-run and never mutates files.

Live restore requires:
  --apply
  --confirmation rollback-fanout-cutover
  --pre-cutover-db PATH
  --pre-cutover-index-dir PATH
  --writers-stopped

Restores pre-cutover DB (+wal/shm if present) and memory.hnsw* from the
side directory created by fanout_cutover_apply.py.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

CONFIRMATION = "rollback-fanout-cutover"


class RollbackError(RuntimeError):
    """Raised when rollback cannot proceed safely."""


def plan_rollback(
    *,
    prod_db: Path,
    pre_cutover_db: Path,
    pre_cutover_index_dir: Path,
    data_dir: Path | None = None,
) -> dict:
    data_dir = data_dir or prod_db.parent
    pre_wal = Path(str(pre_cutover_db) + "-wal")
    pre_shm = Path(str(pre_cutover_db) + "-shm")
    index_sources = (
        sorted(pre_cutover_index_dir.glob("memory.hnsw*"))
        if pre_cutover_index_dir.is_dir()
        else []
    )
    current_indexes = sorted(data_dir.glob("memory.hnsw*"))
    missing: list[str] = []
    if not pre_cutover_db.is_file():
        missing.append("pre_cutover_db")
    if not pre_cutover_index_dir.is_dir():
        missing.append("pre_cutover_index_dir")
    if pre_cutover_index_dir.is_dir() and not index_sources:
        missing.append("pre_cutover_index_files")
    return {
        "mode": "dry-run-rollback",
        "generated_at": time.time(),
        "prod_db": str(prod_db),
        "pre_cutover_db": str(pre_cutover_db),
        "pre_cutover_db_exists": pre_cutover_db.is_file(),
        "pre_cutover_wal_exists": pre_wal.exists(),
        "pre_cutover_shm_exists": pre_shm.exists(),
        "pre_cutover_index_dir": str(pre_cutover_index_dir),
        "pre_cutover_index_files": [p.name for p in index_sources],
        "current_prod_exists": prod_db.is_file(),
        "current_index_files": [p.name for p in current_indexes],
        "missing": missing,
        "ready": not missing,
        "apply_implemented": True,
        "phase2_promote_allowed": False,
        "ordered_ops": [
            "MAINT: stop/drain AstrBot writers",
            "REMOVE: current wave_memory.db (+wal/shm) if present",
            "RESTORE-DB: rename pre_cutover db (+wal/shm) back to wave_memory.db",
            "REMOVE: current memory.hnsw* in data_dir",
            "RESTORE-INDEX: move pre_cutover index files back to data_dir",
            "START: reload plugin / process",
            "SMOKE: formal counts + affinity + FTS",
        ],
    }


def apply_rollback(
    *,
    prod_db: Path,
    pre_cutover_db: Path,
    pre_cutover_index_dir: Path,
    confirmation: str,
    writers_stopped: bool,
    dry_run: bool,
) -> dict:
    plan = plan_rollback(
        prod_db=prod_db,
        pre_cutover_db=pre_cutover_db,
        pre_cutover_index_dir=pre_cutover_index_dir,
    )
    if dry_run:
        plan["switched"] = False
        return plan

    if confirmation != CONFIRMATION:
        raise RollbackError(f"confirmation_required:{CONFIRMATION}")
    if not writers_stopped:
        raise RollbackError("writers_stopped_required")
    if not plan.get("ready"):
        raise RollbackError(f"rollback_not_ready:{plan.get('missing')}")

    actions: list[str] = []
    data_dir = prod_db.parent
    ts = int(time.time())
    failed_aside = data_dir / f"wave_memory.failed_cutover_{ts}"
    failed_aside.mkdir(parents=True, exist_ok=False)

    # Side-aside current (failed) production DB
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(prod_db) + suffix) if suffix else prod_db
        if src.exists():
            dest = failed_aside / src.name
            src.rename(dest)
            actions.append(f"aside_current:{src.name}->{dest}")

    # Restore pre-cutover DB
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(pre_cutover_db) + suffix) if suffix else pre_cutover_db
        if not src.exists():
            if suffix == "":
                raise RollbackError(f"missing_pre_cutover_db:{src}")
            continue
        dest = Path(str(prod_db) + suffix) if suffix else prod_db
        if dest.exists():
            raise RollbackError(f"dest_exists:{dest}")
        src.rename(dest)
        actions.append(f"restore_db:{src.name}->{dest.name}")

    # Aside current indexes then restore
    for p in sorted(data_dir.glob("memory.hnsw*")):
        # skip the failed_aside / pre_cutover dirs themselves
        if p.is_dir():
            continue
        dest = failed_aside / p.name
        p.rename(dest)
        actions.append(f"aside_index:{p.name}")

    for p in sorted(pre_cutover_index_dir.glob("memory.hnsw*")):
        dest = data_dir / p.name
        if dest.exists():
            raise RollbackError(f"index_dest_exists:{dest}")
        # prefer rename within same filesystem; fallback copy+unlink
        try:
            p.rename(dest)
        except OSError:
            shutil.copy2(p, dest)
            p.unlink()
        actions.append(f"restore_index:{p.name}")

    return {
        "mode": "apply-rollback",
        "generated_at": time.time(),
        "switched": True,
        "ok": True,
        "actions": actions,
        "failed_cutover_aside": str(failed_aside),
        "prod_db": str(prod_db),
        "phase2_promote_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prod-db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument("--pre-cutover-db", required=True)
    parser.add_argument("--pre-cutover-index-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    dry_run = not args.apply
    try:
        if dry_run:
            report = plan_rollback(
                prod_db=Path(args.prod_db),
                pre_cutover_db=Path(args.pre_cutover_db),
                pre_cutover_index_dir=Path(args.pre_cutover_index_dir),
            )
            report["ok"] = True
            report["switched"] = False
        else:
            report = apply_rollback(
                prod_db=Path(args.prod_db),
                pre_cutover_db=Path(args.pre_cutover_db),
                pre_cutover_index_dir=Path(args.pre_cutover_index_dir),
                confirmation=args.confirmation,
                writers_stopped=bool(args.writers_stopped),
                dry_run=False,
            )
    except RollbackError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "switched": False,
            "phase2_promote_allowed": False,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
