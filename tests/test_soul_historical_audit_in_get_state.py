from __future__ import annotations

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
from engine.db.scoped_soul_repo import ScopedSoulRepository


def test_get_state_includes_historical_audit_side_channel(tmp_path):
    manager = ConnectionManager(str(tmp_path / "soul_hist.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        scope = RuntimeScope(
            "bot-a",
            "group",
            SessionRef("qq:group:g1", "qq", "group", "g1"),
            subject_principal_id="qq:user:u1",
        )
        repo.upsert_relationship(
            scope,
            subject_principal_id="qq:user:u1",
            affinity=12,
            state="neutral",
            dimensions={"familiarity": 10, "trust": 0, "fun": 0, "hostility": 0, "depth": 0},
            evidence=[
                {"relationship_event_id": 1},
                {
                    "kind": "historical_audit_summary",
                    "summary": "历史审计事件 2 条；类型：direct_reply×1、bot_attacked×1",
                    "affects_affinity": False,
                },
            ],
        )
        manager.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scoped_soul_relationship_legacy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                legacy_event_id TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                bot_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                visibility TEXT NOT NULL,
                group_id TEXT NOT NULL,
                subject_principal_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                dimension TEXT NOT NULL,
                delta REAL NOT NULL,
                reason TEXT NOT NULL,
                occurred_at REAL,
                source_episode_id INTEGER,
                source_memory_id INTEGER,
                source_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            INSERT INTO scoped_soul_relationship_legacy_events(
                legacy_event_id, scope_key, bot_id, session_id, visibility, group_id,
                subject_principal_id, event_type, dimension, delta, reason, occurred_at,
                source_episode_id, source_memory_id, source_hash, event_hash, run_id, created_at
            ) VALUES
            ('1','k','bot-a','qq:group:g1','group','g1','qq:user:u1','direct_reply','familiarity',0.5,'看见',1.0,NULL,NULL,'h','e1','r',1.0),
            ('2','k','bot-a','qq:group:g1','group','g1','qq:user:u1','bot_attacked','hostility',1.0,'攻击',2.0,NULL,NULL,'h','e2','r',2.0);
            """
        )
        state = repo.get_state(scope, subject_principal_id="qq:user:u1", limit=25, offset=0)
        assert state["relationship"]["affinity"] == 12
        summaries = state["relationship"].get("evidence_summaries") or []
        assert summaries
        assert "历史审计事件 2 条" in summaries[0]
        listed = repo.list_relationships(scope, subject_principal_id="qq:user:u1")
        assert listed and listed[0].get("evidence_summaries")
        audit = state["historical_audit"]
        assert audit["available"] is True
        assert audit["total"] == 2
        assert audit["readonly"] is True
        assert audit["affects_affinity"] is False
        assert audit["source_table"] == "scoped_soul_relationship_legacy_events"
        assert "relationship_history" in state
        assert state["relationship_history"]["total"] == 0
    finally:
        manager.close()
