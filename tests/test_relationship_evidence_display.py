"""Unit tests for historical_audit_summary read-only display helpers."""

from __future__ import annotations

import json

from services.relationship_evidence_display import (
    extract_historical_audit_summaries,
    format_evidence_summary_lines,
    relationship_injection_summary_snippet,
)


def test_extract_from_mixed_machine_and_summary():
    evidence = [
        {"relationship_event_id": 1},
        {
            "kind": "historical_audit_summary",
            "summary": "历史审计事件 10 条；类型：direct_reply×10；（只读摘要，不影响亲和分）",
            "affects_affinity": False,
        },
    ]
    texts = extract_historical_audit_summaries(evidence)
    assert len(texts) == 1
    assert "历史审计事件 10 条" in texts[0]


def test_extract_from_json_string():
    raw = json.dumps(
        [{"kind": "historical_audit_summary", "summary": "hello summary"}],
        ensure_ascii=False,
    )
    assert extract_historical_audit_summaries(raw) == ["hello summary"]


def test_empty_without_summary():
    assert extract_historical_audit_summaries([{"relationship_event_id": 9}]) == []
    assert format_evidence_summary_lines([]) == []
    assert relationship_injection_summary_snippet(None) == ""


def test_format_lines_header():
    lines = format_evidence_summary_lines(
        [{"kind": "historical_audit_summary", "summary": "叙事A"}],
        header="可读历史摘要（只读，不改变好感度）",
    )
    assert lines[0].startswith("可读历史摘要")
    assert lines[1] == "- 叙事A"
