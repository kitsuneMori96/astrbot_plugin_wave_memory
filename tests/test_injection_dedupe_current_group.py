from __future__ import annotations

from engine.query_engine import QueryEngine


def test_format_injection_prefers_current_group_and_dedupes_fanout_clones():
    engine = QueryEngine(db=None, memory_index=None, embedding_service=None, config={"min_similarity": 0.0})
    memories = [
        {
            "id": 1,
            "group_id": "g2",
            "sender_name": "甲",
            "sender_id": "u1",
            "content": "现在告诉我我是谁？",
            "timestamp": 100,
            "score": 0.99,
            "source": "chat",
            "_is_cross_group": True,
            "origin_fingerprint": "origin-a",
        },
        {
            "id": 2,
            "group_id": "g1",
            "sender_name": "甲",
            "sender_id": "u1",
            "content": "现在告诉我我是谁？",
            "timestamp": 101,
            "score": 0.90,
            "source": "chat",
            "_is_cross_group": False,
            "origin_fingerprint": "origin-a",
        },
        {
            "id": 3,
            "group_id": "g3",
            "sender_name": "乙",
            "sender_id": "u2",
            "content": "现在告诉我我是谁？",
            "timestamp": 99,
            "score": 0.95,
            "source": "chat",
            "_is_cross_group": True,
            "origin_fingerprint": "origin-a",
        },
        {
            "id": 4,
            "group_id": "g1",
            "sender_name": "丙",
            "sender_id": "u3",
            "content": "另一条本群记忆",
            "timestamp": 102,
            "score": 0.80,
            "source": "chat",
            "_is_cross_group": False,
        },
    ]

    text = engine.format_injection(memories, current_group_id="g1")
    assert text.count("现在告诉我我是谁？") == 1
    assert "[群g2]" not in text
    assert "[群g3]" not in text
    assert "另一条本群记忆" in text
