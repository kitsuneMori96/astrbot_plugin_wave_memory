"""Export AstrBot runtime data copies for backup/restore inspection."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import db_inventory

SQLITE_KINDS = {"sqlite_db", "sqlite_sidecar"}

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _target_relative(kind: str, relative_path: str) -> Path:
    rel = Path(relative_path)
    if kind in SQLITE_KINDS:
        return Path("db") / rel
    if kind == "config":
        return Path("config") / rel
    if kind == "plugin_upload":
        return Path("uploads") / rel
    return Path("other") / rel


def _copy_manifest_file(source_root: Path, target_root: Path, item: dict[str, Any]) -> dict[str, Any]:
    src = source_root / item["relative_path"]
    dst_rel = _target_relative(item["kind"], item["relative_path"])
    dst = target_root / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    exported = dict(item)
    exported.update({
        "export_path": dst_rel.as_posix(),
        "sha256": _sha256(dst),
    })
    return exported


def export_host_data(data_dir: str | Path, target: str | Path, source: str = "host-copy") -> dict[str, Any]:
    root = Path(data_dir)
    target_root = Path(target)
    target_root.mkdir(parents=True, exist_ok=True)
    inventory = db_inventory.inventory_host_data(root)
    files = [_copy_manifest_file(root, target_root, item) for item in inventory["files"]]
    manifest: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "data_dir": str(root),
        "target": str(target_root),
        "files": files,
    }
    (target_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def export_docker_data(
    container_name: str = "astrbot",
    data_dir: str = "/AstrBot/data",
    target: str | Path = "AstrBot-master/data/backups/sqlite_runtime/exports/latest",
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Copy only inventory-included runtime files out of a container."""

    run = runner or _default_runner
    target_root = Path(target)
    target_root.mkdir(parents=True, exist_ok=True)
    inventory = db_inventory.inventory_docker_data(container_name, data_dir, runner=run)
    manifest: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "docker-runtime",
        "container": container_name,
        "data_dir": data_dir,
        "target": str(target_root),
        "files": [],
    }
    if inventory.get("error"):
        manifest["error"] = inventory["error"]
        (target_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    for item in inventory["files"]:
        dst_rel = _target_relative(item["kind"], item["relative_path"])
        dst = target_root / dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        source = f"{container_name}:{data_dir.rstrip('/')}/{item['relative_path']}"
        result = run(
            ["docker", "cp", source, str(dst)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            manifest["error"] = result.stderr.strip() or result.stdout.strip() or f"docker cp failed with {result.returncode}"
            (target_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            return manifest
        exported = dict(item)
        exported.update({
            "export_path": dst_rel.as_posix(),
            "sha256": _sha256(dst),
        })
        manifest["files"].append(exported)

    (target_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export AstrBot runtime data copies with manifest")
    parser.add_argument("--mode", choices=["host-copy", "docker"], default="docker")
    parser.add_argument("--data-dir", default="AstrBot-master/data")
    parser.add_argument("--container", default="astrbot")
    parser.add_argument("--target", default=None)
    parser.add_argument("--timestamped", action="store_true")
    args = parser.parse_args()

    target = args.target
    if target is None:
        base = Path("AstrBot-master/data/backups/sqlite_runtime/exports")
        target = base / (time.strftime("%Y%m%d_%H%M%S") if args.timestamped else "latest")

    if args.mode == "docker":
        manifest = export_docker_data(args.container, "/AstrBot/data", target)
    else:
        manifest = export_host_data(args.data_dir, target)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if manifest.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
