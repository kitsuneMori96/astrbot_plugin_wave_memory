"""Unit-level smoke for multi-scope staged pilot helpers (no docker)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.run_evidence_summary_multi_scope_staged_pilot import (
    _affinity_fp,
    _copy_scope_slice,
    _top_scopes,
)
from scripts.relationship_evidence_summary_dryrun import apply_summaries, plan


def test_copy_and_apply_two_scopes(tmp_path: Path):
    prod = tmp_path / "prod.db"
    conn = sqlite3.connect(prod.as_posix())
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
    for sid, subj, aff in (("s1", "u1", 3), ("s2", "u2", 5)):
        conn.execute(
            "INSERT INTO scoped_soul_relationships VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "yushu",
                sid,
                "group",
                subj,
                aff,
                "n",
                "{}",
                1,
                json.dumps([{"relationship_event_id": 1}]),
                1.0,
            ),
        )
        conn.execute(
            "INSERT INTO scoped_soul_relationship_legacy_events VALUES (NULL,?,?,?,?,?,?,?)",
            ("yushu", sid, "group", subj, "direct_reply", "r", 1.0),
        )
    conn.commit()
    conn.close()

    scopes = _top_scopes(prod, limit=2)
    assert len(scopes) == 2
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    for bot_id, session_id in scopes:
        staged = pilot / f"{session_id}.db"
        counts = _copy_scope_slice(prod, staged, bot_id=bot_id, session_id=session_id)
        assert counts["formal"] == 1
        before = _affinity_fp(staged)
        c = sqlite3.connect(staged.as_posix())
        planned = plan(c, bot_id=bot_id, session_id=session_id, limit=10)
        c.close()
        r = apply_summaries(
            staged,
            planned["candidates"],
            bot_id=bot_id,
            session_id=session_id,
            limit=10,
        )
        assert r["updated"] == 1
        assert _affinity_fp(staged) == before
