from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from domain.scope import RuntimeScope, SessionRef
from engine.db.connection import ConnectionManager
from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
from engine.db.scoped_soul_repo import ScopedSoulRepository
from services.injection.channels.relationship import RelationshipChannel


def scope_for(user_id: str = "u1") -> RuntimeScope:
    return RuntimeScope(
        "bot-alpha",
        "group",
        SessionRef("qq:group:g1", "qq", "group", "g1"),
        subject_principal_id=f"qq:user:{user_id}",
    )


def test_relationship_context_contains_recent_subject_history_but_not_other_user_timeline(tmp_path):
    manager = ConnectionManager(str(tmp_path / "soul.db"))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        current = scope_for("u1")
        other = scope_for("u2")
        repo.record_relationship_event(
            current,
            event_type="deep_talk",
            dimension="depth",
            delta=3,
            reason="一起聊到深夜",
        )
        repo.add_timeline_event(current, event_summary="一起完成了发布", event_type="shared_experience")
        repo.add_timeline_event(other, event_summary="不应泄漏给 u1", event_type="private_experience")
        ctx = SimpleNamespace(
            mode="full",
            config={"channels": {"affinity": {"enabled": True}}},
            scope=current,
        )

        result = asyncio.run(RelationshipChannel(repository=repo).build(ctx))

        assert result.status == "hit"
        assert "一起聊到深夜" in result.text
        assert "一起完成了发布" in result.text
        assert "不应泄漏给 u1" not in result.text
    finally:
        manager.close()


def test_lifecycle_writes_formal_scoped_mood_without_legacy_mood_write():
    from services.lifecycle import LifecycleService

    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE memories(
            bot_id TEXT, session_id TEXT, visibility TEXT, timestamp REAL,
            memory_type TEXT, quarantine INTEGER DEFAULT 0, content TEXT
        )"""
    )
    connection.executemany(
        "INSERT INTO memories VALUES (?, ?, ?, ?, 'message', 0, ?)",
        [
            ("bot-alpha", "qq:group:g1", "group", 100.0, "大家开心地玩梗，真有趣"),
            ("bot-alpha", "qq:group:g1", "group", 101.0, "谢谢你的支持"),
        ],
    )
    connection.commit()

    class Repo:
        def __init__(self):
            self.calls = []

        def upsert_mood(self, scope, **kwargs):
            self.calls.append((scope, kwargs))

    repo = Repo()
    db = SimpleNamespace(conn=connection, soul_repository=repo)
    lifecycle = LifecycleService(
        db,
        bot_qq_id="qq-bot",
        bot_db_id="bot-alpha",
        bot_identities={"bot-alpha": "qq-bot"},
        run_global_jobs=False,
    )
    scope = scope_for("u1")

    assert lifecycle._update_scoped_mood(scope, content="我们继续开心地聊天") is True
    assert len(repo.calls) == 1
    _, payload = repo.calls[0]
    assert payload["policy_version"] == "scoped-mood/v2"
    assert payload["valence"] > 0
    assert payload["evidence"][0]["source"] == "formal_scoped_memories"
    assert not hasattr(db, "set_mood")
    connection.close()
