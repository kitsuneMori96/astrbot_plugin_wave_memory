from __future__ import annotations

import numpy as np

from domain.scope import RuntimeScope, SessionRef
from engine.database import WaveMemoryDB


def _scope(group_id: str = "g1") -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-a",
        visibility="group",
        session=SessionRef(
            id=f"qq:group:{group_id}",
            platform_id="qq",
            kind="group",
            conversation_id=group_id,
        ),
    )


def _memory(db: WaveMemoryDB, scope: RuntimeScope, *, content: str, source: str = "chat") -> int:
    return db.add_memory(
        group_id=scope.session.conversation_id,
        content=content,
        vector=np.asarray([0.1, 0.2, 0.3], dtype=np.float32),
        sender_id="u1",
        sender_name="User",
        timestamp=1_800_000_000.0,
        importance=1.0,
        source=source,
        scope=scope,
        provenance={},
        origin_metadata={},
    )


def test_scoped_cold_candidates_use_effective_tags_and_never_cross_scope(tmp_path):
    db = WaveMemoryDB(str(tmp_path / "cold.sqlite"), dimension=3)
    scope = _scope()
    other_scope = _scope("g2")
    try:
        cold_id = _memory(db, scope, content="当前 Scope 的冷记忆")
        tag_id = db.upsert_scoped_tag(scope, name="冷召回", tag_type="topic", confidence=0.9, metadata={})
        db.link_scoped_memory_tag(scope, memory_id=cold_id, tag_id=tag_id, relevance=0.8)

        other_id = _memory(db, other_scope, content="另一个 Scope 的相同标签")
        other_tag = db.upsert_scoped_tag(other_scope, name="冷召回", tag_type="topic", confidence=0.9, metadata={})
        db.link_scoped_memory_tag(other_scope, memory_id=other_id, tag_id=other_tag, relevance=1.0)

        noise_id = _memory(db, scope, content="不该出现的噪声", source="noise")
        db.link_scoped_memory_tag(scope, memory_id=noise_id, tag_id=tag_id, relevance=1.0)

        rows = db.list_scoped_cold_memory_candidates(scope, [tag_id], limit=8)

        assert [row["id"] for row in rows] == [cold_id]
        assert rows[0]["tag_score"] == 0.8
        assert rows[0]["vector"] is not None
        assert db.list_scoped_cold_memory_candidates(scope, [other_tag], limit=8) == []
    finally:
        db.close()
