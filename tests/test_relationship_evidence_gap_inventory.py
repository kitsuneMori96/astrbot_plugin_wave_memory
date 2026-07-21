"""Unit test for multi-scope evidence gap inventory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.relationship_evidence_gap_inventory import inventory


def test_inventory_counts_candidates(tmp_path: Path):
    db = tmp_path / "i.db"
    conn = sqlite3.connect(db.as_posix())
    conn.executescript(
        """
        CREATE TABLE scoped_soul_relationships(
            bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT, affinity INTEGER, evidence TEXT
        );
        CREATE TABLE scoped_soul_relationship_legacy_events(
            id INTEGER, bot_id TEXT, session_id TEXT, visibility TEXT,
            subject_principal_id TEXT
        );
        """
    )
    # scope A: machine + audit
    conn.execute(
        "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?)",
        ("yushu", "s1", "group", "u1", 1, json.dumps([{"relationship_event_id": 1}])),
    )
    conn.execute(
        "INSERT INTO scoped_soul_relationship_legacy_events VALUES (1,?,?,?,?)",
        ("yushu", "s1", "group", "u1"),
    )
    # scope A: already has summary
    conn.execute(
        "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?)",
        (
            "yushu",
            "s1",
            "group",
            "u2",
            2,
            json.dumps([{"kind": "historical_audit_summary", "summary": "x"}]),
        ),
    )
    # scope B: machine no audit
    conn.execute(
        "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?)",
        ("baizz", "s2", "group", "u3", 0, "[]"),
    )
    conn.commit()
    report = inventory(conn, sample_per_scope=5)
    conn.close()
    assert report["writes_evidence"] is False
    assert report["totals"]["formal"] == 3
    assert report["totals"]["summary_candidates"] == 1
    assert report["totals"]["has_historical_summary"] == 1
    assert report["totals"]["machine_without_audit"] == 1
    assert report["scope_count"] == 2
