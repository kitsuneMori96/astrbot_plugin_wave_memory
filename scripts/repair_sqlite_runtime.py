"""Offline SQLite repair/repack workflow for AstrBot runtime DB files."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

import db_health_check
import db_inventory

try:
    from sqlite_runtime_guard import assert_astrbot_stopped
except ModuleNotFoundError:  # package import from repository root
    from scripts.sqlite_runtime_guard import assert_astrbot_stopped

SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _sidecar_paths(db_path: Path) -> list[Path]:
    return [Path(f"{db_path}{suffix}") for suffix in SIDECAR_SUFFIXES]


def _relative_to_data(path: Path, data_dir: Path) -> Path:
    try:
        return path.relative_to(data_dir)
    except ValueError:
        return Path(path.name)


def _backup_db_and_sidecars(db_path: Path, data_dir: Path, backup_dir: Path) -> None:
    rel = _relative_to_data(db_path, data_dir)
    _copy_if_exists(db_path, backup_dir / rel)
    for sidecar in _sidecar_paths(db_path):
        sidecar_rel = _relative_to_data(sidecar, data_dir)
        _copy_if_exists(sidecar, backup_dir / sidecar_rel)


def _rebuild_with_backup_api(source: Path, rebuilt: Path) -> None:
    src = sqlite3.connect(str(source))
    try:
        # Force SQLite to actually read schema/page content before replacement.
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("source quick_check failed")
        rebuilt.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(rebuilt))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def _write_report(report: dict[str, Any], backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "repair_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def repair_database(
    db_path: str | Path,
    data_dir: str | Path | None = None,
    backup_root: str | Path | None = None,
    check_runtime_stopped: bool = True,
) -> dict[str, Any]:
    if check_runtime_stopped:
        assert_astrbot_stopped("repair SQLite runtime")

    target = Path(db_path)
    root = Path(data_dir) if data_dir is not None else target.parent
    backup_base = Path(backup_root) if backup_root is not None else root / "backups" / "sqlite_runtime"
    backup_dir = backup_base / f"repair_{_timestamp()}_{target.stem}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    rel = _relative_to_data(target, root).as_posix()
    report: dict[str, Any] = {
        "status": "failed",
        "db": rel,
        "path": str(target),
        "backup_dir": str(backup_dir),
        "rebuilt_path": "",
        "health": None,
        "error": "",
    }

    _backup_db_and_sidecars(target, root, backup_dir)

    rebuilt = backup_dir / "db_after" / rel
    try:
        _rebuild_with_backup_api(target, rebuilt)
        health = db_health_check.check_sqlite_db(rebuilt, rel)
        report["health"] = health
        report["rebuilt_path"] = str(rebuilt)
        if health.get("status") != "PASS":
            report["error"] = "rebuilt DB failed health check"
            return report
        shutil.copy2(rebuilt, target)
        for sidecar in _sidecar_paths(target):
            if sidecar.exists():
                sidecar.unlink()
        report["status"] = "repaired"
        report["error"] = ""
        return report
    except Exception as exc:
        report["error"] = str(exc)
        return report
    finally:
        _write_report(report, backup_dir)


def repair_all(data_dir: str | Path, backup_root: str | Path | None = None) -> list[dict[str, Any]]:
    root = Path(data_dir)
    inventory = db_inventory.inventory_host_data(root)
    reports = []
    assert_astrbot_stopped("repair SQLite runtime")
    for item in inventory["files"]:
        if item["kind"] == "sqlite_db":
            reports.append(repair_database(root / item["relative_path"], root, backup_root, check_runtime_stopped=False))
    return reports


def _resolve_db_arg(data_dir: Path, db_name: str) -> Path:
    name = db_name[:-3] if db_name.endswith(".db") else db_name
    return data_dir / f"{name}.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair/repack AstrBot runtime SQLite DBs offline")
    parser.add_argument("--data-dir", default="AstrBot-master/data")
    parser.add_argument("--backup-root", default=None)
    parser.add_argument("--db", help="DB path relative to data-dir without or with .db, e.g. data_v4 or plugin_data/x/y")
    parser.add_argument("--all", action="store_true", help="Repair every discovered DB")
    parser.add_argument("--runtime-mode", choices=["host-copy", "linux", "docker"], default="host-copy")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if args.all:
        result: Any = repair_all(data_dir, args.backup_root)
        ok = all(r.get("status") == "repaired" for r in result)
    elif args.db:
        result = repair_database(_resolve_db_arg(data_dir, args.db), data_dir, args.backup_root)
        ok = result.get("status") == "repaired"
    else:
        parser.error("one of --db or --all is required")

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
