"""Live relationship updates must not drop historical_audit_summary evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.db.scoped_soul_repo import (  # noqa: E402
    _merge_relationship_evidence,
    _parse_evidence_list,
)


def test_merge_preserves_historical_audit_summary() -> None:
    existing = json.dumps(
        [
            {"relationship_event_id": 1},
            {
                "kind": "historical_audit_summary",
                "summary": "历史审计事件 9 条；（只读摘要，不影响亲和分）",
                "affects_affinity": False,
            },
        ],
        ensure_ascii=False,
    )
    merged = _merge_relationship_evidence(
        existing,
        [{"relationship_event_id": 99}, {"dimension": "familiarity", "value_layer": "automatic"}],
    )
    kinds = [x.get("kind") for x in merged if isinstance(x, dict)]
    assert "historical_audit_summary" in kinds
    assert any(x.get("relationship_event_id") == 99 for x in merged)
    summaries = [x for x in merged if x.get("kind") == "historical_audit_summary"]
    assert len(summaries) == 1


def test_merge_none_keeps_existing() -> None:
    existing = '[{"kind":"historical_audit_summary","summary":"s"},{"relationship_event_id":1}]'
    merged = _merge_relationship_evidence(existing, None)
    assert len(merged) == 2
    assert merged[0]["kind"] == "historical_audit_summary"


def test_merge_dedupes_same_summary() -> None:
    existing = [
        {"kind": "historical_audit_summary", "summary": "same"},
    ]
    merged = _merge_relationship_evidence(
        existing,
        [{"kind": "historical_audit_summary", "summary": "same"}, {"relationship_event_id": 2}],
    )
    # caller supplied summary → use fresh path without re-adding old
    assert sum(1 for x in merged if x.get("kind") == "historical_audit_summary") == 1


def test_parse_evidence_list_tolerant() -> None:
    assert _parse_evidence_list(None) == []
    assert _parse_evidence_list("not-json") == []
    assert _parse_evidence_list('{"a":1}') == []


def test_calibrate_relationship_preserves_audit_summary(tmp_path) -> None:
    from domain.scope import RuntimeScope, SessionRef
    from engine.db.connection import ConnectionManager
    from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
    from engine.db.scoped_soul_repo import ScopedSoulRepository

    path = tmp_path / "cal.db"
    manager = ConnectionManager(str(path))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        scope = RuntimeScope(
            "bot-alpha",
            "group",
            SessionRef("qq:group:g1", "qq", "group", "g1"),
            subject_principal_id="qq:user:u1",
        )
        summary = "历史审计事件 5 条；（只读摘要，不影响亲和分）"
        rev = repo.upsert_relationship(
            scope,
            subject_principal_id="qq:user:u1",
            affinity=5,
            dimensions={"familiarity": 5, "trust": 0, "fun": 0, "hostility": 0, "depth": 0},
            evidence=[
                {
                    "kind": "historical_audit_summary",
                    "summary": summary,
                    "affects_affinity": False,
                }
            ],
        )
        # calibrate should not wipe summary on formal row
        repo.calibrate_relationship(
            scope,
            subject_principal_id="qq:user:u1",
            dimension="familiarity",
            action="adjust",
            delta=1.0,
            reason="manual_tune",
            evidence=[{"note": "calibration"}],
            expected_revision=int(rev),
            operation_id="op-test-1",
        )
        state = repo.get_state(scope, subject_principal_id="qq:user:u1", limit=25, offset=0)
        evidence = (state.get("relationship") or {}).get("evidence") or []
        assert any(
            isinstance(x, dict) and x.get("kind") == "historical_audit_summary" for x in evidence
        )
        assert any(isinstance(x, dict) and "calibration_operation_id" in x for x in evidence)
    finally:
        manager.close()


def test_record_relationship_event_preserves_audit_summary(tmp_path) -> None:
    from domain.scope import RuntimeScope, SessionRef
    from engine.db.connection import ConnectionManager
    from engine.db.migrations.scoped_soul import ensure_scoped_soul_schema
    from engine.db.scoped_soul_repo import ScopedSoulRepository

    path = tmp_path / "soul.db"
    manager = ConnectionManager(str(path))
    try:
        ensure_scoped_soul_schema(manager)
        repo = ScopedSoulRepository(manager)
        scope = RuntimeScope(
            "bot-alpha",
            "group",
            SessionRef("qq:group:g1", "qq", "group", "g1"),
            subject_principal_id="qq:user:u1",
        )
        summary = "历史审计事件 3 条；（只读摘要，不影响亲和分）"
        repo.upsert_relationship(
            scope,
            subject_principal_id="qq:user:u1",
            affinity=5,
            dimensions={"familiarity": 5, "trust": 0, "fun": 0, "hostility": 0, "depth": 0},
            evidence=[
                {"relationship_event_id": 1},
                {
                    "kind": "historical_audit_summary",
                    "summary": summary,
                    "affects_affinity": False,
                },
            ],
        )
        # Live automatic event previously wiped evidence; must preserve summary.
        repo.record_relationship_event(
            scope,
            event_type="direct_reply",
            dimension="familiarity",
            delta=1.0,
            reason="live_chat",
            source_episode_id=None,
            source_memory_id=None,
        )
        state = repo.get_state(scope, subject_principal_id="qq:user:u1", limit=25, offset=0)
        rel = state.get("relationship") or {}
        summaries = rel.get("evidence_summaries") or []
        evidence = rel.get("evidence") or []
        assert any(summary in str(s) for s in summaries) or any(
            isinstance(x, dict) and x.get("kind") == "historical_audit_summary" for x in evidence
        )
        assert any(
            isinstance(x, dict) and x.get("kind") == "historical_audit_summary" for x in evidence
        )
        # live fragment also present
        assert any(isinstance(x, dict) and "relationship_event_id" in x for x in evidence)
    finally:
        manager.close()
