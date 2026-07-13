"""R5/R2 fail-closed contracts with one invalid condition over a complete Gate baseline."""

from __future__ import annotations

import sqlite3

from engine.db.learning_repository import LearningRepositories
from services.learning.candidate_service import LearningCandidateService
from services.learning.domain_promotions import (
    FactPromotionService,
    WorldviewInternalizationPromotionService,
)
from tests.system_convergence.contracts import (
    contract_assert,
    load_quality_promotion_adapter,
    reason_code,
)


class _FactSink:
    def __init__(self):
        self.calls: list[dict] = []

    def insert_fact(self, **kwargs):
        self.calls.append(dict(kwargs))
        return 41


class _MemorySink:
    def __init__(self):
        self.calls: list[dict] = []

    def add_memory(self, **kwargs):
        self.calls.append(dict(kwargs))
        return 42


def _formal_scope(bot_id: str = "bot-alpha", group_id: str = "group-alpha") -> dict:
    """Design section 3.1 serialized RuntimeScope; principal is not a SessionRef field."""
    return {
        "bot_id": bot_id,
        "visibility": "group",
        "session": {
            "id": f"qq:group:{group_id}",
            "platform_id": "qq",
            "kind": "group",
            "conversation_id": group_id,
        },
        "subject_principal_id": "qq:user:900000001",
    }


def _evidence_ref(source_scope: dict, *, evidence_id: str) -> dict:
    return {
        "kind": "chat_message",
        "id": evidence_id,
        "content_hash": "sha256:5df14a76f9f739e667b45e32af3a15d756f07e923ce1ad4bdef2f5240f41c040",
        "captured_at": 100.0,
        "source_scope": source_scope,
        "available": True,
    }


def _evidence_binding(evidence_id: str, target_scope: dict) -> dict:
    return {
        "evidence_id": evidence_id,
        "target_scope": target_scope,
        "derivation_chain": ("raw_chat", "reviewed_candidate"),
        "policy_version": "quality-scope/v1",
    }


def _valid_fact_evidence(*, bot_id: str = "bot-alpha", group_id: str = "group-alpha") -> dict:
    target_scope = _formal_scope(bot_id, group_id)
    ref = _evidence_ref(target_scope, evidence_id="message:synthetic-message-1")
    binding = _evidence_binding(ref["id"], target_scope)
    return {
        "subject": "qq:user:900000001",
        "predicate": "likes",
        "object": "tea",
        "group_id": group_id,
        "session_ref": f"qq:group:{group_id}",
        "target_scope": target_scope,
        "evidence_ref": ref,
        "evidence_refs": (ref,),
        "evidence_binding": binding,
        "evidence_bindings": (binding,),
        "provenance": {
            "source": "chat",
            "message_id": "synthetic-message-1",
            "captured_by": "system-convergence-contract",
        },
        "source_fingerprint": "sha256:fact-source-fingerprint-v1",
        "bot_registration": {"db_id": bot_id, "status": "active"},
        "source": {"kind": "chat", "message_id": "synthetic-message-1"},
    }


def _valid_worldview_evidence() -> dict:
    target_scope = _formal_scope()
    ref = _evidence_ref(target_scope, evidence_id="message:synthetic-message-2")
    binding = _evidence_binding(ref["id"], target_scope)
    anchor_ref = _evidence_ref(target_scope, evidence_id="anchor:synthetic-commitment-1")
    return {
        "group_id": "group-alpha",
        "user_id": "qq:user:900000001",
        "session_ref": "qq:group:group-alpha",
        "target_scope": target_scope,
        "evidence_ref": ref,
        "evidence_refs": (ref,),
        "evidence_binding": binding,
        "evidence_bindings": (binding,),
        "provenance": {
            "source": "chat",
            "message_id": "synthetic-message-2",
            "review_status": "reviewed",
        },
        "source_fingerprint": "sha256:worldview-source-fingerprint-v1",
        "bot_registration": {"db_id": "bot-alpha", "status": "active"},
        "importance": 1.0,
        "commitment_level": "high",
        "source": {"kind": "chat", "message_id": "synthetic-message-2"},
        "anchor_ref": anchor_ref,
    }


def _fact_candidate(evidence: dict) -> dict:
    return {
        "id": 1,
        "candidate_type": "fact",
        "content": "Synthetic user likes tea",
        "evidence": evidence,
    }


def _invoke(call):
    try:
        result = call()
    except Exception as exc:
        return reason_code(exc), exc
    return reason_code(result), result


def _assert_real_delegation(adapter, service, operation: str, reason: str) -> None:
    contract_assert(len(adapter.calls) == 1, reason, "quality adapter did not record one call")
    contract_assert(
        adapter.calls[0].service_id == id(service) and adapter.calls[0].operation == operation,
        reason,
        "quality adapter did not invoke the supplied real promotion service",
    )


def _assert_rejected_without_writes(code, expected: str, sink, reason: str) -> None:
    violations = []
    if code != expected:
        violations.append(f"expected typed rejection {expected!r}, got {code!r}")
    if sink.calls:
        violations.append(f"formal sink received writes: {sink.calls!r}")
    contract_assert(not violations, reason, "; ".join(violations))


def test_no_scope_rejects_before_any_domain_write():
    reason = "R2_R5_SCOPE_REQUIRED"
    evidence = _valid_fact_evidence()
    evidence.pop("target_scope")
    evidence.pop("evidence_binding")
    evidence["evidence_bindings"] = ()
    sink = _FactSink()
    service = FactPromotionService(sink)
    adapter = load_quality_promotion_adapter(reason, fact_service=service)
    code, _ = _invoke(
        lambda: adapter.promote_fact(
            candidate=_fact_candidate(evidence), bot_id="bot-alpha", target_kind="fact"
        )
    )
    _assert_real_delegation(adapter, service, "fact.promote", reason)
    _assert_rejected_without_writes(code, "scope_required", sink, reason)


def test_qq_number_as_db_id_rejects_before_any_domain_write():
    reason = "R5_QQ_AS_DB_ID"
    invalid_db_id = "900000001"
    sink = _FactSink()
    service = FactPromotionService(sink)
    adapter = load_quality_promotion_adapter(reason, fact_service=service)
    code, _ = _invoke(
        lambda: adapter.promote_fact(
            candidate=_fact_candidate(_valid_fact_evidence(bot_id=invalid_db_id)),
            bot_id=invalid_db_id,
            target_kind="fact",
        )
    )
    _assert_real_delegation(adapter, service, "fact.promote", reason)
    _assert_rejected_without_writes(code, "invalid_bot_db_id", sink, reason)


def test_default_bot_sentinel_rejects_without_formal_database_row():
    reason = "R5_DEFAULT_BOT_SENTINEL"
    connection = sqlite3.connect(":memory:")
    try:
        repositories = LearningRepositories.from_connection(connection, now=lambda: 1.0)
        before = connection.execute("SELECT COUNT(*) FROM learning_candidates").fetchone()[0]
        code, _ = _invoke(
            lambda: LearningCandidateService(repositories).create(
                bot_id="bot",
                candidate_type="fact",
                content="sentinel identity",
                evidence=_valid_fact_evidence(bot_id="bot"),
                source_fingerprint="default-bot-sentinel",
            )
        )
        after = connection.execute("SELECT COUNT(*) FROM learning_candidates").fetchone()[0]
    finally:
        connection.close()
    violations = []
    if code != "invalid_bot_db_id":
        violations.append(f"sentinel rejection code={code!r}")
    if after != before:
        violations.append(f"formal DB rows changed {before}->{after}")
    contract_assert(not violations, reason, "; ".join(violations))


def test_cross_scope_evidence_rejects_before_any_domain_write():
    reason = "R5_CROSS_SCOPE_EVIDENCE"
    evidence = _valid_fact_evidence()
    # The otherwise valid baseline changes only EvidenceRef.source_scope.
    evidence["evidence_ref"]["source_scope"] = _formal_scope(group_id="group-other")
    sink = _FactSink()
    service = FactPromotionService(sink)
    adapter = load_quality_promotion_adapter(reason, fact_service=service)
    code, _ = _invoke(
        lambda: adapter.promote_fact(
            candidate=_fact_candidate(evidence), bot_id="bot-alpha", target_kind="fact"
        )
    )
    _assert_real_delegation(adapter, service, "fact.promote", reason)
    _assert_rejected_without_writes(code, "evidence_scope_mismatch", sink, reason)


def test_high_commitment_without_anchor_rejects_before_any_domain_write():
    reason = "R5_HIGH_COMMITMENT_ANCHOR"
    sink = _MemorySink()
    evidence = _valid_worldview_evidence()
    evidence.pop("anchor_ref")
    service = WorldviewInternalizationPromotionService(sink)
    adapter = load_quality_promotion_adapter(reason, worldview_service=service)
    code, _ = _invoke(
        lambda: adapter.promote_worldview(
            candidate={
                "id": 9,
                "candidate_type": "worldview_internalization",
                "content": "I will always protect this synthetic principal no matter what.",
                "evidence": evidence,
            },
            bot_id="bot-alpha",
            target_kind="memory",
        )
    )
    _assert_real_delegation(adapter, service, "worldview.promote", reason)
    _assert_rejected_without_writes(code, "anchor_required", sink, reason)
