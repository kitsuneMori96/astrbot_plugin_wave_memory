from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import scope_quality_migration as migration

KEY = b"test-only-scope-quality-approval-key"


def _db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE memories(id INTEGER PRIMARY KEY, content TEXT, bot_id TEXT, session_id TEXT, origin_fingerprint TEXT, quarantine INTEGER DEFAULT 0);
        CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE memory_tags(memory_id INTEGER, tag_id INTEGER);
        CREATE TABLE facts(id INTEGER PRIMARY KEY, source_memory_id INTEGER);
        CREATE TABLE learning_candidates(id INTEGER PRIMARY KEY, legacy_kind TEXT, legacy_ref TEXT, review_status TEXT DEFAULT 'pending', metadata_json TEXT DEFAULT '{}', reviewer TEXT, reviewed_at REAL, review_note TEXT, updated_at REAL);
        CREATE TABLE beliefs(id INTEGER PRIMARY KEY, evidence TEXT, status TEXT DEFAULT 'active', archived_reason TEXT);
        INSERT INTO memories VALUES (1, 'opaque', 'bot-a', 'group-1', 'origin-1', 0);
        INSERT INTO memories VALUES (2, 'unresolved', NULL, NULL, 'origin-2', 0);
        INSERT INTO tags VALUES (1, 'ok');
        INSERT INTO memory_tags VALUES (1, 1);
        INSERT INTO memory_tags VALUES (99, 1);
        INSERT INTO facts VALUES (1, 99);
        INSERT INTO learning_candidates (id, legacy_kind, legacy_ref) VALUES (1, 'old', 'memories:1');
        INSERT INTO beliefs (id, evidence) VALUES (1, NULL);
        """
    )
    conn.commit()
    conn.close()


def _approved(tmp_path: Path) -> tuple[dict, Path, Path]:
    db = tmp_path / "source.db"
    _db(db)
    manifest = migration.preview_database(db, tmp_path / "preview")
    manifest_path = Path(manifest["manifest_path"])
    approval_path = tmp_path / "approval.json"
    migration.approve_manifest(
        manifest_path,
        approval_path,
        key=KEY,
        allowed_actions=["quarantine", "rebuild_derived", "disconnect_reference"],
    )
    return manifest, manifest_path, approval_path


def test_preview_is_deterministic_and_does_not_modify_source(tmp_path):
    db = tmp_path / "source.db"
    _db(db)
    before = db.read_bytes()
    first = migration.preview_database(db, tmp_path / "one")
    second = migration.preview_database(db, tmp_path / "two")

    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["summary"]["total"] == 5
    assert {item["category"] for item in first["items"]} == {
        "missing_scope", "foreign_key_orphan", "learning_legacy_unlinked", "belief_evidence_unavailable"
    }
    assert db.read_bytes() == before


def test_signed_apply_executes_business_changes_and_records_real_row_hashes(tmp_path):
    manifest, manifest_path, approval_path = _approved(tmp_path)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["approval_version"] == 2
    assert approval["snapshot_id"] == manifest["snapshot_id"]
    assert approval["item_count"] == manifest["summary"]["total"]
    assert approval["hmac_sha256"].startswith("hmac-sha256:")

    target = tmp_path / "copy.db"
    run_dir = tmp_path / "run"
    applied = migration.apply_approved_snapshot(
        approval_path, manifest_path, target, run_dir, key=KEY
    )
    assert applied["status"] == "applied_unverified"
    assert applied["before_sha256"].startswith("sha256:")
    assert applied["after_db_sha256"].startswith("sha256:")

    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT quarantine FROM memories WHERE id=2").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM memory_tags").fetchone() == (1,)
        assert conn.execute("SELECT source_memory_id FROM facts WHERE id=1").fetchone() == (None,)
        status, metadata_json = conn.execute(
            "SELECT review_status, metadata_json FROM learning_candidates WHERE id=1"
        ).fetchone()
        assert status == "rejected"
        migration_metadata = json.loads(metadata_json)["scope_quality_migration"]
        assert migration_metadata["disposition"] == "rejected"
        assert migration_metadata["legacy"] == {
            "legacy_kind": "old",
            "legacy_ref": "memories:1",
        }
        assert conn.execute(
            "SELECT status, archived_reason FROM beliefs WHERE id=1"
        ).fetchone() == ("archived", "scope_quality_migration")
        actions = conn.execute(
            "SELECT before_hash, after_hash FROM migration_actions ORDER BY ordinal"
        ).fetchall()
        assert len(actions) == 5
        assert all(before.startswith("sha256:") and after.startswith("sha256:") for before, after in actions)
        assert all(before != manifest["snapshot_id"] for before, _ in actions)
        assert all(before != after for before, after in actions)
    finally:
        conn.close()

    verified = migration.verify_run(run_dir)
    assert verified["status"] == "verified"
    assert verified["actions"] == 5
    assert verified["postcondition_failures"] == []
    assert all(item["ok"] for item in verified["business_postconditions"])

    rolled_back = migration.rollback_run(run_dir)
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["restored_sha256"] == rolled_back["before_sha256"]
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 2
        assert conn.execute("SELECT quarantine FROM memories WHERE id=2").fetchone() == (0,)
    finally:
        conn.close()


def test_apply_requires_key_and_old_v1_approval_fails_closed(tmp_path):
    db = tmp_path / "source.db"
    _db(db)
    manifest = migration.preview_database(db, tmp_path / "preview")
    manifest_path = Path(manifest["manifest_path"])

    signed_path = tmp_path / "signed.json"
    migration.approve_manifest(
        manifest_path,
        signed_path,
        key=KEY,
        allowed_actions=["quarantine", "rebuild_derived", "disconnect_reference"],
    )
    with pytest.raises(ValueError, match="HMAC key is required"):
        migration.apply_approved_snapshot(signed_path, manifest_path, tmp_path / "copy.db", tmp_path / "run")

    v1_path = tmp_path / "v1.json"
    v1 = json.loads(signed_path.read_text(encoding="utf-8"))
    v1["approval_version"] = 1
    v1.pop("hmac_sha256", None)
    v1_path.write_text(json.dumps(v1), encoding="utf-8")
    assert v1["approval_version"] == 1
    with pytest.raises(ValueError, match="approval v1"):
        migration.apply_approved_snapshot(v1_path, manifest_path, tmp_path / "copy-v1.db", tmp_path / "run-v1", key=KEY)


def test_tampered_manifest_or_approval_is_rejected(tmp_path):
    _, manifest_path, approval_path = _approved(tmp_path)
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["rules_version"] = "wrong"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    with pytest.raises(ValueError, match="HMAC"):
        migration.apply_approved_snapshot(
            approval_path, manifest_path, tmp_path / "copy.db", tmp_path / "run", key=KEY
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"][0]["primary_key"] = "999"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest_sha256"):
        migration.apply_approved_snapshot(
            approval_path, manifest_path, tmp_path / "copy-2.db", tmp_path / "run-2", key=KEY
        )


def test_verify_checks_business_postconditions_not_only_audit_rows(tmp_path):
    _, manifest_path, approval_path = _approved(tmp_path)
    target = tmp_path / "copy.db"
    run_dir = tmp_path / "run"
    migration.apply_approved_snapshot(approval_path, manifest_path, target, run_dir, key=KEY)

    conn = sqlite3.connect(target)
    try:
        conn.execute("UPDATE memories SET quarantine=0 WHERE id=2")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM migration_actions").fetchone() == (5,)
    finally:
        conn.close()

    result = migration.verify_run(run_dir)
    assert result["status"] == "failed"
    assert any("business postcondition failed: memories:2" in failure for failure in result["postcondition_failures"])


def test_rollback_rejects_target_drift(tmp_path):
    _, manifest_path, approval_path = _approved(tmp_path)
    target = tmp_path / "copy.db"
    run_dir = tmp_path / "run"
    migration.apply_approved_snapshot(approval_path, manifest_path, target, run_dir, key=KEY)

    conn = sqlite3.connect(target)
    try:
        conn.execute("INSERT INTO tags VALUES (2, 'post-apply')")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="target drift"):
        migration.rollback_run(run_dir)
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT name FROM tags WHERE id=2").fetchone() == ("post-apply",)
    finally:
        conn.close()
