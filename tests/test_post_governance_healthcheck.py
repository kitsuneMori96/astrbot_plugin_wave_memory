"""Unit tests for post_governance_healthcheck (tmpdir only)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.post_governance_healthcheck import check  # noqa: E402


def test_check_on_minimal_db(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "wave_memory.db"
    conn = sqlite3.connect(db.as_posix())
    conn.executescript(
        """
        CREATE TABLE memories(
            id INTEGER PRIMARY KEY,
            bot_id TEXT, session_id TEXT, visibility TEXT, group_id TEXT,
            provenance TEXT, quarantine INTEGER, resolution_state TEXT, content TEXT
        );
        CREATE TABLE scoped_soul_relationships(
            bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT, affinity INTEGER, evidence TEXT
        );
        CREATE TABLE scoped_soul_relationship_legacy_events(
            id INTEGER PRIMARY KEY, bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT
        );
        CREATE TABLE scope_recovery_memory_map(
            legacy_memory_id INTEGER, target_scope_key TEXT, target_memory_id INTEGER
        );
        """
    )
    # clean formalized row
    conn.execute(
        "INSERT INTO memories VALUES (1,'yushu','羽书:group:1','group','1','',0,"
        "'owned_formalized_from_unscoped','hi')"
    )
    # quarantined unscoped noise
    conn.execute(
        "INSERT INTO memories VALUES (2,NULL,NULL,NULL,'1','',1,"
        "'noise_bot_unscoped_quarantine','sys')"
    )
    # 1088 formal would be huge; gate formal_fingerprint_ok will fail — that's ok
    # for unit test we only assert structure
    for i in range(3):
        conn.execute(
            "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?)",
            (
                "yushu",
                "羽书:group:1",
                "group",
                f"羽书:user:{i}",
                1,
                '[{"kind":"historical_audit_summary","summary":"s"}]',
            ),
        )
        conn.execute(
            "INSERT INTO scoped_soul_relationship_legacy_events VALUES (?,?,?,?,?)",
            (i + 1, "yushu", "羽书:group:1", "group", f"羽书:user:{i}"),
        )
    conn.commit()
    conn.close()

    # point PLUGIN checks to repo root which has the real code files
    import scripts.post_governance_healthcheck as mod

    monkeypatch.setattr(mod, "PLUGIN", ROOT)
    report = check(db)
    assert report["metrics"]["fanout_marked"] == 0
    assert report["metrics"]["active_unscoped"] == 0
    assert report["gates"]["fanout_marked_zero"] is True
    assert report["gates"]["active_unscoped_zero"] is True
    assert report["gates"]["evidence_merge_in_code"] is True
    assert report["phase2_promote_allowed"] is False
