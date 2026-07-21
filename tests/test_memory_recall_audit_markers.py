"""MemoryRecall audit items preserve grant/cross markers."""

from __future__ import annotations

from services.injection.channels.memory_recall import _audit_item


def test_audit_item_preserves_shared_grant_and_cross_group():
    item = _audit_item(
        {
            "id": 9,
            "source": "chat",
            "score": 0.8,
            "similarity": 0.7,
            "content": "hello",
            "group_id": "g2",
            "_shared_grant": True,
            "_is_cross_group": True,
        }
    )
    assert item["id"] == 9
    assert item["group_id"] == "g2"
    assert item["_shared_grant"] is True
    assert item["_is_cross_group"] is True


def test_audit_item_without_markers_stays_clean():
    item = _audit_item({"id": 1, "content": "x"})
    assert "_shared_grant" not in item
    assert "_is_cross_group" not in item
