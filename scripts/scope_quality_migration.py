"""受审批、可回滚的 scope/quality 数据迁移。

``preview`` 只读并生成快照；``apply`` 只接受带 HMAC 的 v2 approval，并在快照
副本上执行真实的业务修复。这个脚本不依赖运行时服务，因此可以离线运行。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

RULES_VERSION = "scope-quality-v2"
SCHEMA_VERSION = 2
APPROVAL_VERSION = 2


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return table in _tables(conn)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _require_columns(conn: sqlite3.Connection, table: str, required: set[str]) -> None:
    if not _table_exists(conn, table):
        raise ValueError(f"governance target table is missing: {table}")
    missing = required - _columns(conn, table)
    if missing:
        raise ValueError(f"governance target schema is incomplete: {table} missing {sorted(missing)}")


def _schema_fingerprint(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    return "sha256:" + _sha256_bytes(_canonical([list(row) for row in rows]))


def _snapshot_database(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = _connect_ro(source)
    try:
        target = sqlite3.connect(destination)
        try:
            source_conn.backup(target)
            target.commit()
        finally:
            target.close()
    finally:
        source_conn.close()
    return "sha256:" + _sha256_file(destination)


def _manifest_digest(payload: dict[str, Any]) -> str:
    stable = dict(payload)
    snapshot = dict(stable.get("snapshot") or {})
    snapshot.pop("created_at", None)
    stable["snapshot"] = snapshot
    stable.pop("snapshot_path", None)
    stable.pop("manifest_sha256", None)
    stable.pop("manifest_path", None)
    return "sha256:" + _sha256_bytes(_canonical(stable))


def _items_digest(items: list[dict[str, Any]]) -> str:
    return "sha256:" + _sha256_bytes(_canonical(items))


def _issue(category: str, table: str, key: Any, reason: str, action: str, confidence: str, risk: str) -> dict[str, Any]:
    return {
        "category": category,
        "table": table,
        "primary_key": str(key),
        "row_hash": "sha256:" + _sha256_bytes(_canonical([table, str(key), reason])),
        "reason": reason,
        "suggested_action": action,
        "confidence": confidence,
        "risk": risk,
    }


def _collect_issues(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    tables = _tables(conn)
    issues: list[dict[str, Any]] = []
    if "memories" in tables:
        columns = _columns(conn, "memories")
        if {"bot_id", "session_id"} <= columns:
            for row in conn.execute(
                "SELECT id FROM memories WHERE bot_id IS NULL OR bot_id='' OR session_id IS NULL OR session_id='' ORDER BY id"
            ):
                issues.append(_issue("missing_scope", "memories", row[0], "canonical bot/session scope is unavailable", "quarantine", "high", "high"))
            if "origin_fingerprint" in columns:
                for row in conn.execute(
                    """SELECT id, origin_fingerprint FROM memories
                       WHERE origin_fingerprint IN (
                           SELECT origin_fingerprint FROM memories
                           WHERE origin_fingerprint IS NOT NULL AND origin_fingerprint!=''
                           GROUP BY origin_fingerprint
                           HAVING COUNT(DISTINCT COALESCE(bot_id,'')) > 1
                       )
                       ORDER BY origin_fingerprint, id"""
                ):
                    issue = _issue("cross_bot_collision", "memories", row[0], "same origin fingerprint appears under multiple bots", "quarantine", "high", "high")
                    issue["batch_id"] = "collision:" + _sha256_bytes(str(row[1]).encode("utf-8"))
                    issues.append(issue)
    if "memory_tags" in tables and "memories" in tables and "tags" in tables:
        for row in conn.execute(
            """SELECT mt.rowid FROM memory_tags mt
               WHERE NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=mt.memory_id)
                  OR NOT EXISTS (SELECT 1 FROM tags t WHERE t.id=mt.tag_id)
               ORDER BY mt.rowid"""
        ):
            issues.append(_issue("foreign_key_orphan", "memory_tags", row[0], "memory or tag endpoint is missing", "rebuild_derived", "high", "medium"))
    if "facts" in tables and "memories" in tables and "source_memory_id" in _columns(conn, "facts"):
        for row in conn.execute(
            """SELECT id FROM facts WHERE source_memory_id IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=facts.source_memory_id) ORDER BY id"""
        ):
            issues.append(_issue("foreign_key_orphan", "facts", row[0], "source memory is missing; preserve fact and disconnect reference", "disconnect_reference", "high", "medium"))
    if "learning_candidates" in tables:
        columns = _columns(conn, "learning_candidates")
        if "id" in columns and {"legacy_kind", "legacy_ref"} <= columns:
            evidence_filter = ""
            if "evidence_json" in columns:
                evidence_filter = " AND (evidence_json IS NULL OR TRIM(evidence_json) IN ('', '[]', '{}'))"
            for row in conn.execute(
                "SELECT id FROM learning_candidates WHERE (legacy_kind IS NOT NULL OR legacy_ref IS NOT NULL)" + evidence_filter + " ORDER BY id"
            ):
                issues.append(_issue("learning_legacy_unlinked", "learning_candidates", row[0], "legacy candidate has no canonical evidence binding", "quarantine", "high", "high"))
    if "beliefs" in tables:
        columns = _columns(conn, "beliefs")
        evidence_column = next((name for name in ("evidence_ids", "evidence", "evidence_json", "evidence_refs", "sources") if name in columns), None)
        if evidence_column:
            for row in conn.execute(f"SELECT id FROM beliefs WHERE {evidence_column} IS NULL OR TRIM({evidence_column}) IN ('', '[]', '{{}}') ORDER BY id"):
                issues.append(_issue("belief_evidence_unavailable", "beliefs", row[0], "belief has no resolvable evidence reference", "quarantine", "high", "high"))
    for item in issues:
        keys = _item_keys(conn, item)
        before_hash = _hash_rows(conn, item["table"], keys)
        item["before_hash"] = before_hash
        item["row_hash"] = before_hash  # compatibility alias for v1 readers
        item["item_id"] = "item:" + _sha256_bytes(_canonical([
            item["category"], item["table"], item["primary_key"],
            item["suggested_action"], before_hash,
        ]))
    return issues


def preview_database(db_path: str | Path, output_dir: str | Path, *, rules_version: str = RULES_VERSION, source_label: str | None = None) -> dict[str, Any]:
    source = Path(db_path).resolve()
    if not source.is_file():
        raise ValueError(f"database does not exist: {source}")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    snapshot_path = output / f"snapshot-{_sha256_file(source)[:16]}.db"
    snapshot_hash = _snapshot_database(source, snapshot_path)
    conn = _connect_ro(snapshot_path)
    try:
        schema_fingerprint = _schema_fingerprint(conn)
        issues = _collect_issues(conn)
        counts = {category: sum(1 for issue in issues if issue["category"] == category) for category in sorted({issue["category"] for issue in issues})}
        source_fingerprint = "sha256:" + _sha256_bytes(_canonical({"schema": schema_fingerprint, "snapshot": snapshot_hash, "source": source_label or str(source)}))
    finally:
        conn.close()
    snapshot_id = "snapshot:" + _sha256_bytes(_canonical({"db": snapshot_hash, "schema": schema_fingerprint, "source": source_fingerprint}))
    plan_id = "plan:" + _sha256_bytes(_canonical({"snapshot": snapshot_id, "rules": rules_version}))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan_id,
        "snapshot_id": snapshot_id,
        "snapshot_path": str(snapshot_path),
        "source": {"label": source_label or str(source), "schema_fingerprint": schema_fingerprint, "source_fingerprint": source_fingerprint},
        "snapshot": {"sha256": snapshot_hash, "row_count": _row_count(snapshot_path), "created_at": time.time()},
        "rules_version": rules_version,
        "index": {"generation": None, "manifest_sha256": None},
        "items": issues,
        "summary": {"total": len(issues), "by_category": counts},
    }
    manifest["manifest_sha256"] = _manifest_digest(manifest)
    manifest_path = output / f"{plan_id.removeprefix('plan:')}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _row_count(path: Path) -> int:
    conn = _connect_ro(path)
    try:
        tables = [
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return sum(int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables)
    finally:
        conn.close()


def _read_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = payload.pop("manifest_sha256", None)
    actual = _manifest_digest(payload)
    payload["manifest_sha256"] = expected
    if not expected or expected != actual:
        raise ValueError("manifest_sha256 mismatch")
    return payload


def _normalise_key(key: bytes | str | None) -> bytes | None:
    if key is None:
        return None
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not isinstance(key, bytes) or not key:
        raise ValueError("HMAC key must be non-empty bytes or string")
    return key


def _approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    payload = dict(approval)
    payload.pop("hmac_sha256", None)
    return payload


def _approval_hmac(approval: dict[str, Any], key: bytes) -> str:
    return "hmac-sha256:" + hmac.new(key, _canonical(_approval_payload(approval)), hashlib.sha256).hexdigest()


def approve_manifest(manifest_path: str | Path, output_path: str | Path, *, allowed_actions: list[str] | None = None, exclusions: list[str] | None = None, reviewer: str = "human", key: bytes | str | None = None, hmac_key: bytes | str | None = None) -> dict[str, Any]:
    manifest = _read_manifest(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("legacy_manifest_not_safe_for_apply")
    if key is not None and hmac_key is not None:
        raise ValueError("provide only one of key and hmac_key")
    secret = _normalise_key(key if key is not None else hmac_key)
    if secret is None:
        raise ValueError("HMAC key is required for approval")
    if not allowed_actions:
        raise ValueError("approval requires explicit allowed_actions")
    approval: dict[str, Any] = {
        "approval_version": APPROVAL_VERSION,
        "approval_id": "approval:" + uuid.uuid4().hex,
        "plan_id": manifest["plan_id"],
        "snapshot_id": manifest["snapshot_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "items_sha256": _items_digest(manifest.get("items") or []),
        "item_count": len(manifest.get("items") or []),
        "summary": manifest.get("summary") or {},
        "source_schema_fingerprint": (manifest.get("source") or {}).get("schema_fingerprint"),
        "snapshot_sha256": (manifest.get("snapshot") or {}).get("sha256"),
        "rules_version": manifest["rules_version"],
        "allowed_actions": sorted(set(allowed_actions)),
        "exclusions": sorted(set(exclusions or [])),
        "reviewer": reviewer,
        "approved_at": time.time(),
    }
    if secret is not None:
        approval["hmac_sha256"] = _approval_hmac(approval, secret)
    Path(output_path).write_text(json.dumps(approval, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return approval


def _verify_binding(manifest: dict[str, Any], approval: dict[str, Any], key: bytes | str | None = None) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("legacy_manifest_not_safe_for_apply")
    secret = _normalise_key(key)
    if approval.get("approval_version") != APPROVAL_VERSION:
        raise ValueError("approval v1 is not accepted by apply; create a signed v2 approval")
    if secret is None:
        raise ValueError("HMAC key is required for apply")
    expected_hmac = _approval_hmac(approval, secret)
    if not hmac.compare_digest(str(approval.get("hmac_sha256") or ""), expected_hmac):
        raise ValueError("approval HMAC mismatch")
    expected = {
        "plan_id": manifest.get("plan_id"),
        "snapshot_id": manifest.get("snapshot_id"),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "items_sha256": _items_digest(manifest.get("items") or []),
        "item_count": len(manifest.get("items") or []),
        "summary": manifest.get("summary") or {},
        "source_schema_fingerprint": (manifest.get("source") or {}).get("schema_fingerprint"),
        "snapshot_sha256": (manifest.get("snapshot") or {}).get("sha256"),
        "rules_version": manifest.get("rules_version"),
    }
    for field, value in expected.items():
        if approval.get(field) != value:
            raise ValueError(f"approval binding mismatch: {field}")


def _key_value(item: dict[str, Any]) -> Any:
    key = item["primary_key"]
    return int(key) if key.lstrip("-").isdigit() else key


def _item_keys(conn: sqlite3.Connection, item: dict[str, Any]) -> list[Any]:
    table, key = item["table"], _key_value(item)
    if table == "memory_tags":
        return [key] if conn.execute("SELECT 1 FROM memory_tags WHERE rowid=?", (key,)).fetchone() else []
    if _table_exists(conn, table) and "id" in _columns(conn, table):
        return [key] if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (key,)).fetchone() else []
    return []


def _fetch_row(conn: sqlite3.Connection, table: str, key: Any) -> Any:
    if not _table_exists(conn, table):
        return None
    columns = sorted(_columns(conn, table))
    if table == "memory_tags":
        selected = ", ".join(columns)
        row = conn.execute(
            f"SELECT rowid, {selected} FROM memory_tags WHERE rowid=?", (key,)
        ).fetchone()
        return [["rowid", *columns], list(row)] if row is not None else None
    if "id" not in columns:
        return None
    row = conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE id=?", (key,)).fetchone()
    return [columns, list(row)] if row is not None else None


def _hash_rows(conn: sqlite3.Connection, table: str, keys: list[Any]) -> str:
    rows = [[str(key), _fetch_row(conn, table, key)] for key in sorted(keys, key=str)]
    return "sha256:" + _sha256_bytes(_canonical([table, rows]))


def _set_learning_metadata(conn: sqlite3.Connection, item: dict[str, Any], run_id: str, plan_id: str, now: float, keys: list[Any]) -> None:
    table = "learning_candidates"
    _require_columns(conn, table, {"review_status", "metadata_json"})
    for key in keys:
        candidate_columns = _columns(conn, table)
        selected = ["metadata_json"] + [
            name for name in ("legacy_kind", "legacy_ref") if name in candidate_columns
        ]
        old = conn.execute(
            f"SELECT {', '.join(selected)} FROM learning_candidates WHERE id=?", (key,)
        ).fetchone()
        try:
            metadata = json.loads(old[0] or "{}") if old and old[0] else {}
        except (TypeError, ValueError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        legacy = {
            name: old[index]
            for index, name in enumerate(selected[1:], start=1)
            if old[index] is not None
        }
        metadata["scope_quality_migration"] = {
            "run_id": run_id,
            "plan_id": plan_id,
            "category": item["category"],
            "disposition": "rejected",
            "reason": item["reason"],
            "legacy": legacy,
            "rejected_at": now,
        }
        assignments = ["review_status='rejected'"]
        params: list[Any] = []
        if "reviewer" in _columns(conn, table):
            assignments.append("reviewer=?"); params.append("scope-quality-migration")
        if "reviewed_at" in _columns(conn, table):
            assignments.append("reviewed_at=?"); params.append(now)
        if "review_note" in _columns(conn, table):
            assignments.append("review_note=?"); params.append(item["reason"])
        if "metadata_json" in _columns(conn, table):
            assignments.append("metadata_json=?"); params.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        if "updated_at" in _columns(conn, table):
            assignments.append("updated_at=?"); params.append(now)
        params.append(key)
        conn.execute(f"UPDATE learning_candidates SET {', '.join(assignments)} WHERE id=?", params)


def _execute_item(conn: sqlite3.Connection, item: dict[str, Any], run_id: str, plan_id: str, now: float) -> tuple[str, str, bool]:
    table = item["table"]
    keys = _item_keys(conn, item)
    before_hash = _hash_rows(conn, table, keys)
    expected_before = item.get("before_hash") or item.get("row_hash")
    if expected_before and expected_before != before_hash:
        raise ValueError(f"before row hash mismatch: {table}:{item['primary_key']}")
    if not keys:
        return before_hash, before_hash, False
    if table == "memories":
        _require_columns(conn, table, {"quarantine"})
        for key in keys:
            conn.execute("UPDATE memories SET quarantine=1 WHERE id=?", (key,))
    elif table == "beliefs":
        _require_columns(conn, table, {"status", "archived_reason"})
        for key in keys:
            conn.execute("UPDATE beliefs SET status='archived', archived_reason=? WHERE id=?", ("scope_quality_migration", key))
    elif table == "learning_candidates":
        _set_learning_metadata(conn, item, run_id, plan_id, now, keys)
    elif table == "memory_tags":
        for key in keys:
            conn.execute("DELETE FROM memory_tags WHERE rowid=?", (key,))
    elif table == "facts" and "source_memory_id" in _columns(conn, table):
        for key in keys:
            conn.execute("UPDATE facts SET source_memory_id=NULL WHERE id=?", (key,))
    else:
        return before_hash, before_hash, False
    after_keys = _item_keys(conn, item) if table != "memory_tags" else []
    after_hash = _hash_rows(conn, table, after_keys)
    return before_hash, after_hash, True


@contextlib.contextmanager
def _writer_lease_context(database_path: Path):
    try:
        from engine.writer_lease import WriterLease
    except ImportError as exc:  # pragma: no cover - direct script invocation fallback
        raise ValueError("writer lease support is unavailable") from exc
    try:
        lease = WriterLease.acquire(str(database_path))
    except Exception as exc:
        raise ValueError(f"writer lease unavailable: {database_path}") from exc
    try:
        yield lease
    finally:
        lease.release()


def apply_approved_snapshot(approval_path: str | Path, manifest_path: str | Path, output_db: str | Path, run_dir: str | Path, *, key: bytes | str | None = None, hmac_key: bytes | str | None = None) -> dict[str, Any]:
    target = Path(output_db).resolve()
    with _writer_lease_context(target):
        return _apply_approved_snapshot(approval_path, manifest_path, target, run_dir, key=key, hmac_key=hmac_key)


def _apply_approved_snapshot(approval_path: str | Path, manifest_path: str | Path, output_db: str | Path, run_dir: str | Path, *, key: bytes | str | None = None, hmac_key: bytes | str | None = None) -> dict[str, Any]:
    manifest = _read_manifest(manifest_path)
    approval = json.loads(Path(approval_path).read_text(encoding="utf-8"))
    if key is not None and hmac_key is not None:
        raise ValueError("provide only one of key and hmac_key")
    secret = key if key is not None else hmac_key
    _verify_binding(manifest, approval, secret)
    snapshot = Path(manifest["snapshot_path"])
    if "sha256:" + _sha256_file(snapshot) != manifest["snapshot"]["sha256"]:
        raise ValueError("snapshot hash mismatch")
    target = Path(output_db).resolve()
    run = Path(run_dir).resolve()
    run.mkdir(parents=True, exist_ok=True)
    before = run / "before.db"
    if target.exists():
        _snapshot_database(target, before)
    else:
        shutil.copy2(snapshot, before)
    before_hash = "sha256:" + _sha256_file(before)
    staging = run / f".target-{uuid.uuid4().hex}.db"
    shutil.copy2(snapshot, staging)
    conn = sqlite3.connect(staging)
    action_rows: list[dict[str, Any]] = []
    run_id = "run:" + uuid.uuid4().hex
    now = time.time()
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS migration_runs (
                run_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, before_snapshot_id TEXT NOT NULL,
                after_snapshot_id TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS migration_actions (
                run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, category TEXT NOT NULL,
                table_name TEXT NOT NULL, primary_key TEXT NOT NULL, action TEXT NOT NULL,
                before_hash TEXT NOT NULL, after_hash TEXT NOT NULL, PRIMARY KEY(run_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS migration_quarantine (
                run_id TEXT NOT NULL, category TEXT NOT NULL, table_name TEXT NOT NULL,
                primary_key TEXT NOT NULL, reason TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(run_id, category, table_name, primary_key)
            );
        """)
        conn.execute("INSERT INTO migration_runs VALUES (?, ?, ?, ?, 'running', ?)", (run_id, manifest["plan_id"], manifest["snapshot_id"], "pending", now))
        allowed = set(approval.get("allowed_actions") or [])
        exclusions = set(approval.get("exclusions") or [])
        for ordinal, item in enumerate(manifest["items"], 1):
            action = str(item["suggested_action"])
            if action not in allowed or item["item_id"] in exclusions:
                continue
            before_row_hash, after_row_hash, changed = _execute_item(conn, item, run_id, manifest["plan_id"], now)
            if not changed:
                continue
            conn.execute("INSERT OR IGNORE INTO migration_quarantine VALUES (?, ?, ?, ?, ?, ?)", (run_id, item["category"], item["table"], item["primary_key"], item["reason"], now))
            conn.execute("INSERT INTO migration_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (run_id, ordinal, item["category"], item["table"], item["primary_key"], action, before_row_hash, after_row_hash))
            action_rows.append({"ordinal": ordinal, "item": item, "action": action, "before_hash": before_row_hash, "after_hash": after_row_hash})
        conn.commit()
        after_snapshot_id = "snapshot:" + _sha256_bytes(_canonical({"db": "sha256:" + _sha256_file(staging), "plan": manifest["plan_id"], "run": run_id}))
        conn.execute("UPDATE migration_runs SET after_snapshot_id=?, status='applied_unverified' WHERE run_id=?", (after_snapshot_id, run_id))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        with contextlib.suppress(OSError):
            staging.unlink()
        raise
    finally:
        conn.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, target)
    after_hash = "sha256:" + _sha256_file(target)
    metadata = {"report_schema_version": 2, "run_id": run_id, "target_db": str(target), "before_db": str(before), "plan_id": manifest["plan_id"], "snapshot_id": manifest["snapshot_id"], "manifest_path": str(Path(manifest_path).resolve()), "approval_path": str(Path(approval_path).resolve()), "manifest_sha256": manifest["manifest_sha256"], "status": "applied_unverified", "before_sha256": before_hash, "after_db_sha256": after_hash, "expected_actions": action_rows}
    (run / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return metadata


def _postcondition(conn: sqlite3.Connection, item: dict[str, Any]) -> bool:
    table = item["table"]
    keys = _item_keys(conn, item)
    if table == "memories":
        return bool(keys) and "quarantine" in _columns(conn, table) and all(conn.execute("SELECT COALESCE(quarantine,0) FROM memories WHERE id=?", (key,)).fetchone()[0] == 1 for key in keys)
    if table == "beliefs":
        return bool(keys) and all(
            conn.execute(
                "SELECT status, archived_reason FROM beliefs WHERE id=?", (key,)
            ).fetchone()
            == ("archived", "scope_quality_migration")
            for key in keys
        )
    if table == "learning_candidates":
        if not keys or "review_status" not in _columns(conn, table) or "metadata_json" not in _columns(conn, table):
            return False
        for key in keys:
            status, raw_metadata = conn.execute(
                "SELECT review_status, metadata_json FROM learning_candidates WHERE id=?",
                (key,),
            ).fetchone()
            try:
                metadata = json.loads(raw_metadata or "{}")
            except (TypeError, ValueError):
                return False
            if status != "rejected" or not isinstance(metadata.get("scope_quality_migration"), dict):
                return False
        return True
    if table == "memory_tags":
        return not keys
    if table == "facts":
        return bool(keys) and all(conn.execute("SELECT source_memory_id FROM facts WHERE id=?", (key,)).fetchone()[0] is None for key in keys)
    return False


def verify_run(run_dir: str | Path) -> dict[str, Any]:
    run = Path(run_dir)
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    target = Path(metadata["target_db"])
    conn = sqlite3.connect(target)
    failures: list[str] = []
    postconditions: list[dict[str, Any]] = []
    try:
        quick_rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check")]
        quick = "ok" if quick_rows == ["ok"] else ";".join(quick_rows)
        foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
        actions = conn.execute("SELECT ordinal, category, table_name, primary_key, before_hash, after_hash FROM migration_actions WHERE run_id=? ORDER BY ordinal", (metadata["run_id"],)).fetchall()
        if quick != "ok": failures.append("quick_check failed")
        if foreign_keys: failures.append(f"foreign_key_check={len(foreign_keys)}")
        for expected in metadata.get("expected_actions", []):
            item = expected["item"]
            ok = _postcondition(conn, item)
            postconditions.append({"table": item["table"], "primary_key": item["primary_key"], "category": item["category"], "ok": ok})
            if not ok: failures.append(f"business postcondition failed: {item['table']}:{item['primary_key']}")
            keys = _item_keys(conn, item)
            actual_hash = _hash_rows(conn, item["table"], keys)
            if actual_hash != expected["after_hash"]: failures.append(f"after row hash mismatch: {item['table']}:{item['primary_key']}")
        if len(actions) != len(metadata.get("expected_actions", [])):
            failures.append("migration action audit count mismatch")
        actual_db_hash = "sha256:" + _sha256_file(target)
        if metadata.get("after_db_sha256") != actual_db_hash: failures.append("target database hash drifted")
    finally:
        conn.close()
    result = {"run_id": metadata["run_id"], "status": "verified" if not failures else "failed", "quick_check": quick, "foreign_key_violations": len(foreign_keys), "actions": len(actions), "db_sha256": "sha256:" + _sha256_file(target), "business_postconditions": postconditions, "postcondition_failures": failures}
    (run / "verify.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def rollback_run(run_dir: str | Path) -> dict[str, Any]:
    metadata = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    with _writer_lease_context(Path(metadata["target_db"]).resolve()):
        return _rollback_run(run_dir)


def _rollback_run(run_dir: str | Path) -> dict[str, Any]:
    run = Path(run_dir)
    metadata = json.loads((run / "run.json").read_text(encoding="utf-8"))
    before = Path(metadata["before_db"])
    target = Path(metadata["target_db"])
    if not before.is_file():
        raise ValueError("before snapshot is missing")
    if not metadata.get("after_db_sha256"):
        raise ValueError("run metadata lacks target hash; refusing unsafe rollback")
    current_hash = "sha256:" + _sha256_file(target) if target.is_file() else None
    if current_hash != metadata["after_db_sha256"]:
        raise ValueError("target drift detected; refusing rollback")
    temporary = run / f".rollback-{uuid.uuid4().hex}.db"
    _snapshot_database(before, temporary)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    restored = "sha256:" + _sha256_file(target)
    result = {"run_id": metadata["run_id"], "status": "rolled_back", "restored_sha256": restored, "before_sha256": "sha256:" + _sha256_file(before)}
    (run / "rollback.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _cli_key(args: Any) -> bytes:
    if getattr(args, "hmac_key", None):
        return _normalise_key(args.hmac_key)  # type: ignore[return-value]
    key_file = getattr(args, "hmac_key_file", None)
    if key_file:
        return _normalise_key(Path(key_file).read_bytes())  # type: ignore[return-value]
    env_name = getattr(args, "hmac_key_env", None)
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return _normalise_key(value)  # type: ignore[return-value]
    raise ValueError("HMAC key is required; use --hmac-key-file or the configured environment variable")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview"); preview.add_argument("--db", required=True); preview.add_argument("--output-dir", required=True); preview.add_argument("--rules-version", default=RULES_VERSION)
    approve = sub.add_parser("approve"); approve.add_argument("--manifest", required=True); approve.add_argument("--output", required=True); approve.add_argument("--reviewer", required=True); approve.add_argument("--allow-action", action="append", required=True); approve.add_argument("--exclude-item", action="append", default=[]); approve.add_argument("--hmac-key", default=None); approve.add_argument("--hmac-key-file", default=None); approve.add_argument("--hmac-key-env", default="WAVE_SCOPE_MIGRATION_HMAC_KEY")
    apply = sub.add_parser("apply"); apply.add_argument("--approval", required=True); apply.add_argument("--manifest", required=True); apply.add_argument("--output-db", required=True); apply.add_argument("--run-dir", required=True); apply.add_argument("--hmac-key", default=None); apply.add_argument("--hmac-key-file", default=None); apply.add_argument("--hmac-key-env", default="WAVE_SCOPE_MIGRATION_HMAC_KEY")
    verify = sub.add_parser("verify"); verify.add_argument("--run-dir", required=True)
    rollback = sub.add_parser("rollback"); rollback.add_argument("--run-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "preview": result = preview_database(args.db, args.output_dir, rules_version=args.rules_version)
    elif args.command == "approve": result = approve_manifest(args.manifest, args.output, reviewer=args.reviewer, allowed_actions=args.allow_action, exclusions=args.exclude_item, key=_cli_key(args))
    elif args.command == "apply": result = apply_approved_snapshot(args.approval, args.manifest, args.output_db, args.run_dir, key=_cli_key(args))
    elif args.command == "verify": result = verify_run(args.run_dir)
    else: result = rollback_run(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"verified", "rolled_back"} or args.command in {"preview", "approve", "apply"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
