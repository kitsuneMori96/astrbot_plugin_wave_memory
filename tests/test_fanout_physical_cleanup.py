"""Unit tests for staged fanout physical cleanup (no production writes)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.fanout_physical_cleanup import (  # noqa: E402
    CONFIRMATION,
    FanoutPhysicalCleanupError,
    apply_cleanup,
    is_production_db_path,
    plan_cleanup,
)


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path.as_posix())
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            content TEXT,
            provenance TEXT
        );
        CREATE TABLE scope_recovery_memory_map (
            legacy_memory_id INTEGER,
            target_scope_key TEXT,
            target_memory_id INTEGER,
            origin_key TEXT,
            run_id TEXT
        );
        CREATE TABLE memory_tags (
            memory_id INTEGER,
            tag TEXT
        );
        CREATE TABLE facts (
            id INTEGER PRIMARY KEY,
            source_memory_id INTEGER,
            body TEXT
        );
        """
    )
    # legacy unmarked origin
    conn.execute(
        "INSERT INTO memories(id, content, provenance) VALUES (1, 'origin', ?)",
        (json.dumps({"source": "legacy"}),),
    )
    # six fanout targets
    for mid in range(10, 16):
        conn.execute(
            "INSERT INTO memories(id, content, provenance) VALUES (?, 'clone', ?)",
            (
                mid,
                json.dumps(
                    {
                        "projection_kind": "fanout_duplicate",
                        "fanout_family_id": "legacy:1",
                        "legacy_memory_id": 1,
                    },
                    separators=(",", ":"),
                ),
            ),
        )
        conn.execute(
            "INSERT INTO scope_recovery_memory_map(legacy_memory_id, target_scope_key, target_memory_id, origin_key, run_id) VALUES (1, ?, ?, 'o', 'r')",
            (f"scope-{mid}", mid),
        )
        conn.execute("INSERT INTO memory_tags(memory_id, tag) VALUES (?, 't')", (mid,))
        conn.execute(
            "INSERT INTO facts(source_memory_id, body) VALUES (?, 'f')",
            (mid,),
        )
    # unrelated unmarked map target (must not delete)
    conn.execute(
        "INSERT INTO memories(id, content, provenance) VALUES (99, 'solo', ?)",
        (json.dumps({"source": "owned"}),),
    )
    conn.execute(
        "INSERT INTO scope_recovery_memory_map(legacy_memory_id, target_scope_key, target_memory_id, origin_key, run_id) VALUES (99, 'solo', 99, 'o', 'r')"
    )
    conn.commit()
    conn.close()


def test_is_production_db_path_detects_live_name(tmp_path: Path) -> None:
    prodish = tmp_path / "plugin_data" / "astrbot_plugin_wave_memory" / "wave_memory.db"
    prodish.parent.mkdir(parents=True)
    prodish.write_text("", encoding="utf-8")
    staged = tmp_path / "plugin_data" / "astrbot_plugin_wave_memory" / "wave_memory.fanout-cleanup.sqlite3"
    staged.write_text("", encoding="utf-8")
    assert is_production_db_path(prodish) is True
    assert is_production_db_path(staged) is False


def test_plan_and_apply_keeps_legacy_deletes_marked(tmp_path: Path) -> None:
    db = tmp_path / "wave_memory.fanout-cleanup.sqlite3"
    _init_db(db)

    plan = plan_cleanup(db)
    assert plan["delete_count"] == 6
    assert plan["apply_allowed_here"] is True
    assert 1 not in plan["delete_ids_preview"] or plan["delete_count"] == 6

    with pytest.raises(FanoutPhysicalCleanupError):
        apply_cleanup(db, confirmation="wrong")

    result = apply_cleanup(db, confirmation=CONFIRMATION)
    assert result["memories_deleted"] == 6
    assert result["remaining_marked"] == 0
    assert result.get("fts_status") in {"rebuilt", "skipped_no_fts_memories"}

    conn = sqlite3.connect(db.as_posix())
    ids = {r[0] for r in conn.execute("SELECT id FROM memories").fetchall()}
    assert 1 in ids
    assert 99 in ids
    assert ids.isdisjoint(set(range(10, 16)))
    assert conn.execute("SELECT COUNT(*) FROM memory_tags").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
    # multi-target family map rows removed with cascade on target_memory_id
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM scope_recovery_memory_map WHERE legacy_memory_id=1"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_apply_refuses_production_path(tmp_path: Path) -> None:
    prod = tmp_path / "plugin_data" / "astrbot_plugin_wave_memory" / "wave_memory.db"
    prod.parent.mkdir(parents=True)
    _init_db(prod)
    with pytest.raises(FanoutPhysicalCleanupError, match="production_apply_forbidden"):
        apply_cleanup(prod, confirmation=CONFIRMATION)
