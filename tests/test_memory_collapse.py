from __future__ import annotations

from engine.memory_collapse import collapse_key, collapse_memories, is_fanout_duplicate
from engine.query_engine import QueryEngine


def test_collapse_same_text_despite_different_origin_fingerprints():
    """Historical fanout copies often have unique origin hashes; still collapse."""
    memories = [
        {
            "id": 10,
            "group_id": "g2",
            "sender_id": "u1",
            "content": "你又卡了吗？",
            "origin_fingerprint": "sha256:aaa",
            "score": 0.9,
            "timestamp": 1,
            "_is_cross_group": True,
        },
        {
            "id": 11,
            "group_id": "g1",
            "sender_id": "u1",
            "content": "你又卡了吗？",
            "origin_fingerprint": "sha256:bbb",
            "score": 0.5,
            "timestamp": 2,
            "_is_cross_group": False,
        },
        {
            "id": 12,
            "group_id": "g3",
            "sender_id": "u1",
            "content": "你又卡了吗？",
            "origin_fingerprint": "sha256:ccc",
            "score": 0.8,
            "timestamp": 3,
            "_is_cross_group": True,
        },
    ]
    out = collapse_memories(memories, current_group_id="g1")
    assert len(out) == 1
    assert out[0]["id"] == 11
    assert collapse_key(memories[0]) == collapse_key(memories[1])


def test_collapse_prefers_current_group_and_marked_fanout_family():
    memories = [
        {
            "id": 1,
            "group_id": "g2",
            "content": "现在告诉我我是谁？",
            "score": 0.99,
            "timestamp": 10,
            "_is_cross_group": True,
            "provenance": {
                "projection_kind": "fanout_duplicate",
                "fanout_family_id": "legacy:9",
            },
        },
        {
            "id": 2,
            "group_id": "g1",
            "content": "现在告诉我我是谁？",
            "score": 0.80,
            "timestamp": 11,
            "_is_cross_group": False,
            "provenance": {
                "projection_kind": "fanout_duplicate",
                "fanout_family_id": "legacy:9",
            },
        },
        {
            "id": 3,
            "group_id": "g3",
            "content": "另一条",
            "score": 0.70,
            "timestamp": 12,
            "_is_cross_group": True,
        },
    ]
    out = collapse_memories(memories, current_group_id="g1")
    assert [m["id"] for m in out] == [2, 3]
    assert is_fanout_duplicate(memories[0]) is True
    assert collapse_key(memories[0]) == "family:legacy:9"


def test_query_engine_collapses_before_top_k_window():
    engine = QueryEngine(db=None, memory_index=None, embedding_service=None, config={"min_similarity": 0.0})
    memories = [
        {
            "id": i,
            "group_id": f"g{i}",
            "sender_id": "u",
            "content": "同句",
            "score": 1.0 - i * 0.01,
            "similarity": 0.9,
            "timestamp": 100 - i,
            "source": "chat",
            "_is_cross_group": i != 1,
            "origin_fingerprint": "same-origin",
        }
        for i in range(1, 8)
    ]
    # force current group preference for id=1
    memories[0]["group_id"] = "current"
    memories[0]["_is_cross_group"] = False
    collapsed = engine._prefer_current_group_and_dedupe(memories, current_group_id="current")
    assert len(collapsed) == 1
    assert collapsed[0]["id"] == 1


def test_cold_candidate_collapse_prefers_current_group_family():
    from domain.scope import RuntimeScope, SessionRef
    from engine.recall_policy import RecallPolicy
    import numpy as np

    engine = QueryEngine(db=None, memory_index=None, embedding_service=None, config={"min_similarity": 0.0, "cold_recall_enabled": False})
    scope = RuntimeScope("yushu", "group", SessionRef("羽书:group:g1", "羽书", "group", "g1"))
    policy = RecallPolicy(scope=scope, cross_group_enabled=True)
    # Directly exercise collapse path used at end of cold search via public helper.
    paired = [
        ({"id": 1, "group_id": "g2", "content": "同一冷记忆", "provenance": {"projection_kind": "fanout_duplicate", "fanout_family_id": "legacy:7"}}, 0.1),
        ({"id": 2, "group_id": "g1", "content": "同一冷记忆", "provenance": {"projection_kind": "fanout_duplicate", "fanout_family_id": "legacy:7"}}, 0.2),
        ({"id": 3, "group_id": "g3", "content": "另一条", "provenance": {}}, 0.15),
    ]
    collapse_input = []
    for memory, distance in paired:
        item = dict(memory)
        item["score"] = 1.0 - float(distance)
        item["_is_cross_group"] = policy.is_cross_group(item)
        collapse_input.append(item)
    out = collapse_memories(collapse_input, current_group_id="g1")
    assert [m["id"] for m in out] == [2, 3]
