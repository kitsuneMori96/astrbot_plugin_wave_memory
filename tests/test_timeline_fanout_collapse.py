from __future__ import annotations

from services.injection.channels.timeline import TimelineChannel


def test_timeline_collapses_same_summary_across_groups_preferring_current():
    items = [
        {"summary": "群友讨论修仙", "group_id": "g2", "day": "2026-07-12", "timestamp": 100.0},
        {"summary": "群友讨论修仙", "group_id": "g1", "day": "2026-07-12", "timestamp": 99.0},
        {"summary": "群友讨论修仙", "group_id": "g3", "day": "2026-07-12", "timestamp": 98.0},
        {"summary": "另一件事", "group_id": "g2", "day": "2026-07-11", "timestamp": 90.0},
    ]
    out = TimelineChannel._collapse_summary_fanout(items, current_group_id="g1")
    assert len(out) == 2
    assert out[0]["summary"] == "群友讨论修仙"
    assert out[0]["group_id"] == "g1"
    assert out[1]["summary"] == "另一件事"
