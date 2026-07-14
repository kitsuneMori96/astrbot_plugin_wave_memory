from __future__ import annotations

from domain.evidence import EvidenceRef
from domain.quality import QualityDecision
from domain.scope import RuntimeScope, SessionRef
from webui.api_contract import (
    contract_value,
    field_value_state,
    mutation_response,
)


def _runtime_scope() -> RuntimeScope:
    return RuntimeScope(
        bot_id="bot-a",
        session=SessionRef(kind="group", id="platform-a:group:group-a", platform_id="platform-a", conversation_id="group-a"),
        visibility="group",
    )


def test_mutation_response_exposes_real_operation_and_revision() -> None:
    payload = mutation_response(
        operation_kind="memory.update",
        operation_id="op-1",
        status="succeeded",
        revision=3,
        item={"id": 7},
        include_item=True,
        preflight_token="preflight-1",
    )

    assert payload == {
        "ok": True,
        "operation": {"kind": "memory.update", "status": "succeeded", "id": "op-1"},
        "revision": 3,
        "item": {"id": 7},
        "preflight_token": "preflight-1",
    }


def test_mutation_response_does_not_claim_success_for_queued_work() -> None:
    payload = mutation_response(operation_kind="maintenance.rebuild", status="queued", revision=None)

    assert payload["ok"] is False
    assert payload["operation"]["status"] == "queued"
    assert payload["revision"] is None


def test_field_value_state_keeps_default_saved_effective_distinct() -> None:
    assert field_value_state(
        default=True,
        saved=False,
        effective=True,
        apply_mode="restart",
        effective_since="2026-03-01T00:00:00Z",
    ) == {
        "default": True,
        "saved": False,
        "effective": True,
        "apply_mode": "restart",
        "effective_since": "2026-03-01T00:00:00Z",
    }
    assert field_value_state(default=1, saved=2, effective=2, apply_mode="invalid")["apply_mode"] == "unknown"


def test_contract_value_serializes_scope_evidence_and_quality_contracts() -> None:
    scope = _runtime_scope()
    evidence = EvidenceRef(
        kind="message",
        id="message-1",
        content_hash="sha256:abc",
        captured_at=1.0,
        source_scope=scope,
        available=True,
    )
    decision = QualityDecision(
        proposal_id="proposal-1",
        outcome="allow",
        reason_code="quality_allow",
        rule_version="1",
        normalized_content="content",
    )

    assert contract_value(scope)["bot_id"] == "bot-a"
    assert contract_value(evidence)["source_scope"]["session"]["id"] == "platform-a:group:group-a"
    assert contract_value(decision)["outcome"] == "allow"
