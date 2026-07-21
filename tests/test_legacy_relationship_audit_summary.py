from __future__ import annotations

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.scoped_soul_repo import ScopedSoulRepository


def test_list_legacy_relationship_audit_summary_readonly(tmp_path):
    manager = ConnectionManager(str(tmp_path / "audit.db"))
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
        ('2','k','bot-a','qq:group:g1','group','g1','qq:user:u1','direct_reply','familiarity',0.5,'看见',2.0,NULL,NULL,'h','e2','r',2.0),
        ('3','k','bot-a','qq:group:g1','group','g1','qq:user:u1','bot_attacked','hostility',1.0,'攻击',3.0,NULL,NULL,'h','e3','r',3.0),
        ('4','k','bot-a','qq:group:g1','group','g1','qq:user:u2','direct_reply','familiarity',0.5,'别的人',4.0,NULL,NULL,'h','e4','r',4.0);
        """
    )
    repo = ScopedSoulRepository(manager)
    scope = RuntimeScope(
        "bot-a",
        "group",
        SessionRef("qq:group:g1", "qq", "group", "g1"),
        subject_principal_id="qq:user:u1",
    )
    summary = repo.list_legacy_relationship_audit_summary(scope, recent_limit=2)
    assert summary["available"] is True
    assert summary["total"] == 3
    assert summary["by_type"][0]["event_type"] == "direct_reply"
    assert summary["by_type"][0]["count"] == 2
    assert len(summary["recent"]) == 2
    assert summary["recent"][0]["event_type"] == "bot_attacked"
