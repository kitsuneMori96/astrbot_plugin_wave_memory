"""Runtime SQLite health checks for AstrBot data directories."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import db_inventory


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def check_sqlite_db(path: str | Path, relative_path: str | None = None) -> dict[str, Any]:
    db_path = Path(path)
    result: dict[str, Any] = {
        "relative_path": relative_path or db_path.name,
        "path": str(db_path),
        "exists": db_path.exists(),
        "size": db_path.stat().st_size if db_path.exists() else 0,
        "open": "failed",
        "quick_check": None,
        "integrity_check": None,
        "journal_mode": None,
        "backup_api_copy": "failed",
        "status": "FAIL",
    }

    if not db_path.exists():
        result["error"] = "file does not exist"
        return result

    conn: sqlite3.Connection | None = None
    try:
        conn = _connect_readonly(db_path)
        result["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        result["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        result["open"] = "ok"

        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_path = Path(tmp_dir) / "backup_api_copy.db"
            dst = sqlite3.connect(backup_path)
            try:
                conn.backup(dst)
                result["backup_api_copy"] = "ok"
            finally:
                dst.close()

        if (
            result["open"] == "ok"
            and result["quick_check"] == "ok"
            and result["integrity_check"] == "ok"
            and result["backup_api_copy"] == "ok"
        ):
            result["status"] = "PASS"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        if conn is not None:
            conn.close()


def check_host_data(data_dir: str | Path) -> dict[str, Any]:
    inventory = db_inventory.inventory_host_data(data_dir)
    root = Path(data_dir)
    databases = [
        check_sqlite_db(root / item["relative_path"], item["relative_path"])
        for item in inventory["files"]
        if item["kind"] == "sqlite_db"
    ]
    status = "PASS" if all(item["status"] == "PASS" for item in databases) else "FAIL"
    return {
        "mode": inventory.get("mode", "host-copy"),
        "data_dir": str(root),
        "status": status,
        "databases": databases,
    }


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def check_docker_data(
    container_name: str = "astrbot",
    data_dir: str = "/AstrBot/data",
    runner: Runner | None = None,
) -> dict[str, Any]:
    run = runner or _default_runner
    code = r'''
import json
import sqlite3
import tempfile
from pathlib import Path

EXCLUDED_DIRS = {"__pycache__", "backups", "backups_host"}
EXCLUDED_SUFFIXES = {".pyc"}
EXCLUDED_NAME_MARKERS = ("_backup", "backup_", "_before_", "before_", "_broken_", "broken_", "_repacked_", "repacked_", "_test", "test_", "scratch_")
SQLITE_SIDECAR_SUFFIXES = (".db-wal", ".db-shm", ".db-journal")

def classify_relative_path(relative_path: str) -> str:
    path = Path(relative_path)
    posix = path.as_posix()
    name = path.name
    if name.endswith(".db"):
        return "sqlite_db"
    if name.endswith(SQLITE_SIDECAR_SUFFIXES):
        return "sqlite_sidecar"
    if posix.startswith("plugins/") and "/files/" in posix:
        return "plugin_upload"
    if posix.startswith("config/"):
        return "config"
    return "other"

def _is_excluded_backup_or_test_file(path: Path) -> bool:
    name = path.name.lower()
    if not (name.endswith(".db") or name.endswith(SQLITE_SIDECAR_SUFFIXES)):
        return False
    return any(marker in name for marker in EXCLUDED_NAME_MARKERS)

def should_include(relative_path: str) -> bool:
    path = Path(relative_path)
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if _is_excluded_backup_or_test_file(path):
        return False
    return classify_relative_path(relative_path) != "other"

def sqlite_db(path: Path, relative_path: str) -> dict:
    result = {"relative_path": relative_path, "path": str(path), "exists": path.exists(), "size": path.stat().st_size if path.exists() else 0, "open": "failed", "quick_check": None, "integrity_check": None, "journal_mode": None, "backup_api_copy": "failed", "status": "FAIL"}
    if not path.exists():
        result["error"] = "file does not exist"
        return result
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        result["quick_check"] = conn.execute("PRAGMA quick_check").fetchone()[0]
        result["integrity_check"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
        result["journal_mode"] = conn.execute("PRAGMA journal_mode").fetchone()[0]
        result["open"] = "ok"
        with tempfile.TemporaryDirectory() as tmp_dir:
            dst = sqlite3.connect(Path(tmp_dir) / "backup_api_copy.db")
            try:
                conn.backup(dst)
                result["backup_api_copy"] = "ok"
            finally:
                dst.close()
        if result["quick_check"] == "ok" and result["integrity_check"] == "ok" and result["backup_api_copy"] == "ok":
            result["status"] = "PASS"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
    finally:
        if conn is not None:
            conn.close()

def inventory(root: Path) -> dict:
    files = []
    dbs = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if not should_include(relative):
            continue
        item = {"relative_path": relative, "path": str(path), "kind": classify_relative_path(relative), "size": path.stat().st_size}
        files.append(item)
        if item["kind"] == "sqlite_db":
            dbs.append(sqlite_db(path, relative))
    status = "PASS" if all(item["status"] == "PASS" for item in dbs) else "FAIL"
    return {"mode": "docker", "data_dir": str(root), "status": status, "databases": dbs, "files": files}

root = Path(r''' + repr(data_dir) + r''')
print(json.dumps(inventory(root), ensure_ascii=False))
'''
    result = run(
        ["docker", "exec", container_name, "python", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return {
            "mode": "docker",
            "container": container_name,
            "data_dir": data_dir,
            "status": "FAIL_RUNTIME",
            "databases": [],
            "error": result.stderr.strip() or result.stdout.strip() or f"docker exec failed with {result.returncode}",
        }
    payload = json.loads(result.stdout)
    payload["mode"] = "docker"
    payload["container"] = container_name
    payload["data_dir"] = data_dir
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Check AstrBot runtime SQLite DB health")
    parser.add_argument("--mode", choices=["host-copy", "linux-runtime", "docker"], required=True)
    parser.add_argument("--data-dir", default="AstrBot-master/data")
    parser.add_argument("--container", default="astrbot")
    args = parser.parse_args()

    if args.mode == "docker":
        report = check_docker_data(args.container, args.data_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report.get("status") == "PASS" else 1

    report = check_host_data(args.data_dir)
    report["mode"] = args.mode
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
