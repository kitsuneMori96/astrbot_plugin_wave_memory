"""Unit tests for point-fix missing evidence summary refill (no production)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.refill_missing_evidence_summaries import (  # noqa: E402
    CONFIRMATION,
    apply_missing,
    find_missing,
    main,
)


def _mk(path: Path) -> None:
    conn = sqlite3.connect(path.as_posix())
    conn.executescript(
        """
        CREATE TABLE scoped_soul_relationships(
            bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT, affinity INTEGER, state TEXT,
            dimensions TEXT, revision INTEGER, evidence TEXT, updated_at REAL
        );
        CREATE TABLE scoped_soul_relationship_legacy_events(
            id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT, event_type TEXT, reason TEXT, occurred_at REAL
        );
        """
    )
    # missing summary but has audit
    conn.execute(
        """
        INSERT INTO scoped_soul_relationships VALUES
        ('yushu','羽书:group:1','group','羽书:user:1',9,'neutral','{}',2,
         '[{"relationship_event_id":1}]',1.0)
        """
    )
    conn.execute(
        """
        INSERT INTO scoped_soul_relationship_legacy_events VALUES
        (1,'yushu','羽书:group:1','group','羽书:user:1','direct_reply','看见一条群友消息',1.0)
        """
    )
    # already has summary -> not missing
    conn.execute(
        """
        INSERT INTO scoped_soul_relationships VALUES
        ('yushu','羽书:group:1','group','羽书:user:2',1,'neutral','{}',1,
         '[{"kind":"historical_audit_summary","summary":"已有"}]',1.0)
        """
    )
    conn.execute(
        """
        INSERT INTO scoped_soul_relationship_legacy_events VALUES
        (2,'yushu','羽书:group:1','group','羽书:user:2','direct_reply','x',1.0)
        """
    )
    # no audit -> not missing
    conn.execute(
        """
        INSERT INTO scoped_soul_relationships VALUES
        ('yushu','羽书:group:1','group','羽书:user:3',0,'neutral','{}',1,'[]',1.0)
        """
    )
    conn.commit()
    conn.close()


def test_find_missing_only_audit_without_summary(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _mk(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    missing = find_missing(conn)
    conn.close()
    assert len(missing) == 1
    assert missing[0]["subject_principal_id"] == "羽书:user:1"
    assert missing[0]["audit_total"] == 1
    assert "historical_audit_summary" in missing[0]["proposed_evidence_append"]["kind"]
    assert "历史审计事件 1 条" in missing[0]["proposed_evidence_append"]["summary"]


def test_apply_missing_preserves_affinity_revision(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _mk(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    missing = find_missing(conn)
    conn.close()
    result = apply_missing(db, missing)
    assert result["updated"] == 1
    assert result["summaries_before"] == 1
    assert result["summaries_after"] == 2
    conn = sqlite3.connect(db.as_posix())
    row = conn.execute(
        """
        SELECT affinity, revision, evidence FROM scoped_soul_relationships
         WHERE subject_principal_id='羽书:user:1'
        """
    ).fetchone()
    conn.close()
    assert row[0] == 9
    assert row[1] == 2
    data = json.loads(row[2])
    assert any(x.get("kind") == "historical_audit_summary" for x in data)
    assert any(x.get("relationship_event_id") == 1 for x in data)


def test_main_refuse_prod_like(tmp_path: Path, monkeypatch) -> None:
    # Use a path that looks like prod
    fake_prod = tmp_path / "plugin_data" / "wave_memory.db"
    fake_prod.parent.mkdir(parents=True)
    _mk(fake_prod)
    # rewrite path string check uses name + plugin_data
    # Our helper requires plugin_data in posix path - ok
    report = tmp_path / "refuse.json"
    rc = main(
        [
            "--db",
            str(fake_prod),
            "--apply",
            "--confirmation",
            CONFIRMATION,
            "--report",
            str(report),
        ]
    )
    assert rc == 2
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert "refuse_prod_apply" in str(payload.get("apply_error") or "")
