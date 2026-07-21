"""Unit tests for bot unscoped noise quarantine dry-run."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.quarantine_bot_unscoped_noise_dryrun import (  # noqa: E402
    CONFIRMATION,
    apply_quarantine,
    plan,
)


def _mk(db: Path) -> None:
    conn = sqlite3.connect(db.as_posix())
    conn.executescript(
        """
        CREATE TABLE memories(
            id INTEGER PRIMARY KEY,
            group_id TEXT,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT,
            bot_id TEXT,
            session_id TEXT,
            quarantine INTEGER,
            resolution_state TEXT,
            importance REAL
        );
        """
    )
    conn.execute(
        "INSERT INTO memories VALUES (1,'g1','bot','羽书','系统句',NULL,NULL,0,'',1.0)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (2,'g1','u1','人','人言',NULL,NULL,0,'',1.0)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (3,'g1','bot','羽书','已隔离',NULL,NULL,1,'x',1.0)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (4,'g1','bot','羽书','有scope','yushu','羽书:group:1',0,'',1.0)"
    )
    conn.commit()
    conn.close()


def test_plan_only_unscoped_bot(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _mk(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    p = plan(conn)
    conn.close()
    ids = {c["id"] for c in p["candidates"]}
    assert ids == {1}
    assert p["confirmation_for_apply"] == CONFIRMATION


def test_apply_sets_quarantine(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    _mk(db)
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    p = plan(conn)
    conn.close()
    r = apply_quarantine(db, p["candidates"])
    assert r["updated"] == 1
    conn = sqlite3.connect(db.as_posix())
    row = conn.execute(
        "SELECT quarantine, resolution_state FROM memories WHERE id=1"
    ).fetchone()
    human = conn.execute(
        "SELECT quarantine FROM memories WHERE id=2"
    ).fetchone()[0]
    scoped_bot = conn.execute(
        "SELECT quarantine FROM memories WHERE id=4"
    ).fetchone()[0]
    conn.close()
    assert row == (1, "noise_bot_unscoped_quarantine")
    assert int(human or 0) == 0
    assert int(scoped_bot or 0) == 0
