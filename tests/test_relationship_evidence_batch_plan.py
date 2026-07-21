"""Unit tests for multi-scope evidence batch plan."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.relationship_evidence_batch_plan import batch_plan, list_scopes


def _seed(conn: sqlite3.Connection) -> None:
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
    for sid, subject, n_events in (
        ("s1", "u1", 5),
        ("s1", "u2", 2),
        ("s2", "u3", 3),
    ):
        conn.execute(
            "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "yushu",
                sid,
                "group",
                subject,
                1,
                "n",
                "{}",
                1,
                json.dumps([{"relationship_event_id": 1}]),
                1.0,
            ),
        )
        for i in range(n_events):
            conn.execute(
                "INSERT INTO scoped_soul_relationship_legacy_events VALUES (NULL,?,?,?,?,?,?,?)",
                ("yushu", sid, "group", subject, "direct_reply", "x", float(i)),
            )
    conn.commit()


def test_batch_plan_aggregates_scopes(tmp_path: Path):
    db = tmp_path / "b.db"
    conn = sqlite3.connect(db.as_posix())
    _seed(conn)
    scopes = list_scopes(conn)
    assert len(scopes) == 2
    report = batch_plan(conn, per_scope_limit=10, min_audit=1)
    conn.close()
    assert report["writes_affinity"] is False
    assert report["scope_count"] == 2
    assert report["totals"]["batch_candidates"] == 3
    assert report["batch_size"] == 3
    assert all(b["proposed_evidence_append"]["affects_affinity"] is False for b in report["batch"])
