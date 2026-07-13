import json
import sqlite3

from engine.db.migrations.learning_center import ensure_learning_schema
from services.learning.legacy import (
    BASELINE_COUNTS,
    LEGACY_PROJECTION_LABELS,
    migrate_legacy,
    read_legacy_projections,
)


def _legacy_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            group_id TEXT,
            sender_id TEXT,
            sender_name TEXT,
            content TEXT,
            source TEXT,
            timestamp REAL
        )"""
    )
    conn.execute(
        """CREATE TABLE experience_episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            user_id TEXT,
            episode_type TEXT NOT NULL,
            trigger_text TEXT,
            bot_inner_thought TEXT,
            bot_action TEXT,
            bot_reply TEXT,
            user_reaction TEXT,
            outcome TEXT,
            source_memory_ids TEXT,
            emotional_weight REAL,
            created_at REAL NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE review_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_type TEXT NOT NULL,
            content TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending',
            promoted INTEGER NOT NULL DEFAULT 0
        )"""
    )
    for memory_id, source in (
        (1, "bzz_pending"),
        (2, "bzz_pending"),
        (3, "bzz_evolution"),
        (4, "bzz_experience"),
    ):
        conn.execute(
            "INSERT INTO memories (id, content, source, timestamp) VALUES (?, ?, ?, ?)",
            (memory_id, f"legacy-{memory_id}", source, float(memory_id)),
        )
    conn.execute(
        """INSERT INTO experience_episodes
           (bot_id, group_id, episode_type, bot_reply, created_at)
           VALUES ('baizz', 'g1', 'bot_reply', '互动经历', 10)"""
    )
    conn.execute(
        """INSERT INTO experience_episodes
           (bot_id, group_id, episode_type, bot_reply, created_at)
           VALUES ('yushu', 'g2', 'bot_reply', '其他 Bot 经历', 11)"""
    )
    conn.execute(
        "INSERT INTO review_candidates (candidate_type, content) VALUES ('fact', '旧候选')"
    )
    conn.commit()
    ensure_learning_schema(conn)
    return conn


def test_legacy_backfill_is_idempotent_and_never_fakes_evidence_or_promotions():
    conn = _legacy_db()

    first = migrate_legacy(conn, now=lambda: 100.0)
    second = migrate_legacy(conn, now=lambda: 200.0)

    assert first["start_counts"]["bzz_pending"] == 2
    assert first["start_counts"]["bzz_evolution"] == 1
    assert first["start_counts"]["bzz_experience"] == 1
    assert first["start_counts"]["experience_episodes"] == 1
    assert first["start_counts"]["review_candidates"] == 1
    assert first["differences"]["review_candidates"] == 1 - BASELINE_COUNTS["review_candidates"]
    assert first["created_candidates"] == 2
    assert second["created_candidates"] == 0
    assert second["existing_candidates"] == 2

    rows = conn.execute(
        """SELECT bot_id, candidate_type, review_status, legacy_kind, legacy_ref,
                  source_fingerprint, content, evidence_json, metadata_json
             FROM learning_candidates ORDER BY id"""
    ).fetchall()
    assert len(rows) == 2
    for bot_id, candidate_type, review_status, kind, ref, fingerprint, content, evidence_raw, metadata_raw in rows:
        assert (bot_id, candidate_type, review_status, kind) == (
            "baizz", "worldview_internalization", "pending", "bzz_pending"
        )
        assert ref.startswith("memories:")
        assert fingerprint == ref
        assert content.startswith("legacy-")
        evidence = json.loads(evidence_raw)
        metadata = json.loads(metadata_raw)
        assert evidence["legacy"] is True
        assert evidence["source_memory_id"] == int(ref.split(":", 1)[1])
        assert evidence["traceability"] == "unavailable"
        assert "community_id" not in evidence
        assert "chapter_ref" not in evidence
        assert "original_quote" not in evidence
        assert "participants" not in evidence
        assert "informed_perspective" not in evidence
        assert metadata["projection_label"] == LEGACY_PROJECTION_LABELS["worldview_internalization"]
    assert conn.execute("SELECT COUNT(*) FROM learning_promotions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM memories WHERE source='bzz_pending'").fetchone()[0] == 2


def test_snapshot_watermark_excludes_pending_added_after_migration_started():
    conn = _legacy_db()

    def add_during_migration(connection):
        connection.execute(
            "INSERT INTO memories (id, content, source, timestamp) VALUES (99, 'new-during-run', 'bzz_pending', 99)"
        )
        connection.commit()

    report = migrate_legacy(conn, now=lambda: 300.0, after_snapshot=add_during_migration)

    assert report["watermarks"]["bzz_pending_max_id"] == 2
    assert report["created_candidates"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM memories WHERE source='bzz_pending' AND id=99"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM learning_candidates WHERE legacy_ref='memories:99'"
    ).fetchone()[0] == 0


def test_legacy_history_is_a_read_only_compatibility_projection_with_separate_semantics():
    conn = _legacy_db()

    projection = read_legacy_projections(conn, bot_id="baizz")

    assert [item["source"] for item in projection["evolution_history"]] == ["bzz_evolution"]
    assert [item["source"] for item in projection["legacy_experience_history"]] == ["bzz_experience"]
    assert [item["bot_id"] for item in projection["interaction_experiences"]] == ["baizz"]
    assert projection["evolution_history"][0]["projection_kind"] == "effective_history"
    assert projection["legacy_experience_history"][0]["projection_kind"] == "legacy_history_experience"
    assert projection["interaction_experiences"][0]["projection_kind"] == "interaction_experience"

    before = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    conn.execute("SELECT COUNT(*) FROM learning_candidates").fetchone()
    after = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    assert before == after
