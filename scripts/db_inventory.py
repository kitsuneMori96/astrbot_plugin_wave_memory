"""Inventory AstrBot runtime SQLite and restore-relevant data files."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

SQLITE_SIDECAR_SUFFIXES = (".db-wal", ".db-shm", ".db-journal")
EXCLUDED_DIRS = {"__pycache__", "backups", "backups_host"}
EXCLUDED_SUFFIXES = {".pyc"}
EXCLUDED_NAME_MARKERS = (
    "_backup",
    "backup_",
    "_before_",
    "before_",
    "_broken_",
    "broken_",
    "_repacked_",
    "repacked_",
    "_test",
    "test_",
    "scratch_",
)


def _to_posix(path: Path) -> str:
    return path.as_posix()


def classify_relative_path(relative_path: str) -> str:
    path = Path(relative_path)
    posix = _to_posix(path)
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


def inventory_host_data(data_dir: str | Path) -> dict[str, Any]:
    root = Path(data_dir)
    files: list[dict[str, Any]] = []
    if not root.exists():
        return {"mode": "host-copy", "data_dir": str(root), "files": []}

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = _to_posix(path.relative_to(root))
        if not should_include(relative):
            continue
        files.append({
            "relative_path": relative,
            "path": str(path),
            "kind": classify_relative_path(relative),
            "size": path.stat().st_size,
        })

    return {"mode": "host-copy", "data_dir": str(root), "files": files}


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def inventory_docker_data(
    container_name: str = "astrbot",
    data_dir: str = "/AstrBot/data",
    runner: Runner | None = None,
) -> dict[str, Any]:
    run = runner or _default_runner
    code = r'''
import json
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

def inventory(root: Path) -> dict:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if not should_include(relative):
            continue
        files.append({"relative_path": relative, "path": str(path), "kind": classify_relative_path(relative), "size": path.stat().st_size})
    return {"mode": "docker", "data_dir": str(root), "files": files}

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
            "files": [],
            "error": result.stderr.strip() or result.stdout.strip() or f"docker exec failed with {result.returncode}",
        }
    payload = json.loads(result.stdout)
    payload["mode"] = "docker"
    payload["container"] = container_name
    payload["data_dir"] = data_dir
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory AstrBot runtime DB and restore-relevant files")
    parser.add_argument("--mode", choices=["host-copy", "linux-runtime", "docker"], required=True)
    parser.add_argument("--data-dir", default="AstrBot-master/data")
    parser.add_argument("--container", default="astrbot")
    args = parser.parse_args()

    if args.mode == "docker":
        inventory = inventory_docker_data(args.container, args.data_dir)
        print(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True))
        return 1 if inventory.get("error") else 0

    inventory = inventory_host_data(args.data_dir)
    inventory["mode"] = args.mode
    print(json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
