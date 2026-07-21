from __future__ import annotations

from types import SimpleNamespace

from domain.scope import RuntimeScope, SessionRef
from webui.blueprints import people as people_bp_module


class _Repo:
    def list_legacy_relationship_audit_summary(self, scope, **_kwargs):
        assert scope.subject_principal_id == "羽书:user:u1"
        return {
            "available": True,
            "total": 3,
            "by_type": [{"event_type": "direct_reply", "count": 3}],
            "recent": [
                {
                    "event_type": "direct_reply",
                    "dimension": "familiarity",
                    "delta": 0.5,
                    "reason": "看见一条群友消息",
                }
            ],
        }


def test_historical_audit_summary_helper_is_readonly_side_channel():
    scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("羽书:group:398291136", "羽书", "group", "398291136"),
    )
    summary = people_bp_module._historical_audit_summary_for_subject(
        _Repo(),
        scope,
        "羽书:user:u1",
    )
    assert summary["available"] is True
    assert summary["total"] == 3
    assert summary["readonly"] is True
    assert summary["affects_affinity"] is False
    assert summary["source_table"] == "scoped_soul_relationship_legacy_events"


def test_historical_audit_summary_helper_fail_closed_without_repo_api():
    scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("羽书:group:398291136", "羽书", "group", "398291136"),
    )
    summary = people_bp_module._historical_audit_summary_for_subject(
        SimpleNamespace(),
        scope,
        "羽书:user:u1",
    )
    assert summary["available"] is False
    assert summary["total"] == 0
    assert summary["affects_affinity"] is False


def test_formal_evidence_summaries_extracts_historical_audit_summary():
    texts = people_bp_module._formal_evidence_summaries(
        {
            "evidence": [
                {"relationship_event_id": 1},
                {
                    "kind": "historical_audit_summary",
                    "summary": "历史审计事件 12 条；类型：direct_reply×12",
                    "affects_affinity": False,
                },
            ]
        }
    )
    assert texts == ["历史审计事件 12 条；类型：direct_reply×12"]
    assert people_bp_module._formal_evidence_summaries({"evidence": []}) == []
    assert people_bp_module._formal_evidence_summaries(None) == []
