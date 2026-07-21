from __future__ import annotations

import json
from pathlib import Path

from scripts.fanout_cutover_rollback import CONFIRMATION, RollbackError, apply_rollback, plan_rollback


def test_plan_rollback_reports_missing(tmp_path: Path):
    prod = tmp_path / "wave_memory.db"
    prod.write_bytes(b"cur")
    plan = plan_rollback(
        prod_db=prod,
        pre_cutover_db=tmp_path / "missing.db",
        pre_cutover_index_dir=tmp_path / "missing_idx",
    )
    assert plan["ready"] is False
    assert "pre_cutover_db" in plan["missing"]
    assert plan["switched"] is False if "switched" in plan else True


def test_rollback_dry_run_does_not_move_files(tmp_path: Path):
    prod = tmp_path / "wave_memory.db"
    pre = tmp_path / "wave_memory.pre_cutover_1.db"
    idx = tmp_path / "memory.hnsw.pre_cutover_1"
    idx.mkdir()
    prod.write_bytes(b"new")
    pre.write_bytes(b"old")
    (idx / "memory.hnsw.manifest.json").write_text("{}", encoding="utf-8")
    (idx / "memory.hnsw.g1").write_bytes(b"idx")
    (tmp_path / "memory.hnsw.manifest.json").write_text("cur", encoding="utf-8")

    report = apply_rollback(
        prod_db=prod,
        pre_cutover_db=pre,
        pre_cutover_index_dir=idx,
        confirmation="",
        writers_stopped=False,
        dry_run=True,
    )
    assert report["mode"] == "dry-run-rollback"
    assert report["ready"] is True
    assert report["switched"] is False
    assert prod.read_bytes() == b"new"
    assert pre.read_bytes() == b"old"


def test_rollback_apply_restores_pre_cutover(tmp_path: Path):
    prod = tmp_path / "wave_memory.db"
    pre = tmp_path / "wave_memory.pre_cutover_9.db"
    idx = tmp_path / "memory.hnsw.pre_cutover_9"
    idx.mkdir()
    prod.write_bytes(b"failed-new")
    (tmp_path / "wave_memory.db-wal").write_bytes(b"wal-new")
    pre.write_bytes(b"good-old")
    (tmp_path / "wave_memory.pre_cutover_9.db-wal").write_bytes(b"wal-old")
    (idx / "memory.hnsw.manifest.json").write_text("old-manifest", encoding="utf-8")
    (tmp_path / "memory.hnsw.manifest.json").write_text("new-manifest", encoding="utf-8")

    report = apply_rollback(
        prod_db=prod,
        pre_cutover_db=pre,
        pre_cutover_index_dir=idx,
        confirmation=CONFIRMATION,
        writers_stopped=True,
        dry_run=False,
    )
    assert report["ok"] is True
    assert report["switched"] is True
    assert prod.read_bytes() == b"good-old"
    assert (tmp_path / "wave_memory.db-wal").read_bytes() == b"wal-old"
    assert (tmp_path / "memory.hnsw.manifest.json").read_text(encoding="utf-8") == "old-manifest"
    # pre-cutover sources consumed
    assert not pre.exists()


def test_rollback_requires_confirmation(tmp_path: Path):
    prod = tmp_path / "wave_memory.db"
    pre = tmp_path / "pre.db"
    idx = tmp_path / "idx"
    idx.mkdir()
    prod.write_bytes(b"n")
    pre.write_bytes(b"o")
    (idx / "memory.hnsw.g1").write_bytes(b"i")
    try:
        apply_rollback(
            prod_db=prod,
            pre_cutover_db=pre,
            pre_cutover_index_dir=idx,
            confirmation="wrong",
            writers_stopped=True,
            dry_run=False,
        )
        assert False, "expected RollbackError"
    except RollbackError as exc:
        assert "confirmation_required" in str(exc)
