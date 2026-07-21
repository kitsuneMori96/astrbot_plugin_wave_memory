"""RelationshipChannel injects historical_audit_summary when present."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from domain.scope import RuntimeScope, SessionRef
from services.injection.channels.relationship import RelationshipChannel


class _Repo:
    def __init__(self, state):
        self._state = state

    def get_state(self, scope, subject_principal_id=None, limit=25, offset=0):
        return self._state


def _scope():
    return RuntimeScope(
        bot_id="yushu",
        visibility="group",
        session=SessionRef("qq:group:g1", "qq", "group", "g1"),
        subject_principal_id="qq:user:1",
    )


def test_channel_appends_evidence_summary():
    state = {
        "relationship": {
            "affinity": 12,
            "state": "neutral",
            "dimensions": {"familiarity": 10},
            "values": {},
            "revision": 3,
            "evidence": [
                {"relationship_event_id": 1},
                {
                    "kind": "historical_audit_summary",
                    "summary": "历史审计事件 3786 条；类型：direct_reply×3786",
                    "affects_affinity": False,
                },
            ],
        },
        "relationship_history": {"items": []},
        "timeline": {"items": []},
        "revision": 3,
    }
    ch = RelationshipChannel(repository=_Repo(state))
    ctx = SimpleNamespace(
        mode="full",
        config={"channels": {"affinity": {"enabled": True}}},
        scope=_scope(),
    )
    result = asyncio.run(ch.build(ctx))
    assert result.status == "hit"
    assert "历史关系摘要" in result.text
    assert "3786" in result.text
    assert "好感度" not in result.text or "综合值=12" in result.text


def test_channel_without_summary_still_works():
    state = {
        "relationship": {
            "affinity": 1,
            "state": "neutral",
            "dimensions": {},
            "values": {},
            "revision": 1,
            "evidence": [{"relationship_event_id": 9}],
        },
        "relationship_history": {"items": []},
        "timeline": {"items": []},
    }
    ch = RelationshipChannel(repository=_Repo(state))
    ctx = SimpleNamespace(
        mode="full",
        config={"channels": {"affinity": {"enabled": True}}},
        scope=_scope(),
    )
    result = asyncio.run(ch.build(ctx))
    assert result.status == "hit"
    assert "历史关系摘要" not in result.text
