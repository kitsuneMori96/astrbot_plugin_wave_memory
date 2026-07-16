"""FewShot 正式候选的 Scope/Evidence/Tag/trace 合约。"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

try:
    from ...domain.evidence import EvidenceBinding, EvidenceRef
    from ...domain.scope import RuntimeScope
except ImportError:  # pragma: no cover
    from domain.evidence import EvidenceBinding, EvidenceRef
    from domain.scope import RuntimeScope

from .scope_policy import LearningPromotionScopeError, resolve_learning_promotion_scope

FEWSHOT_CANDIDATE_TYPE = "few_shot_style"
FEWSHOT_CONTRACT_VERSION = "fewshot-reply/v1"
FEWSHOT_BINDING_POLICY = "fewshot-reply-evidence/v1"
FEWSHOT_DERIVATION_CHAIN = ("raw_bot_reply", "quality_assessed", "scoped_candidate")


class FewShotContractError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = str(code)
        self.reason_code = self.code
        super().__init__(message or self.code)


@dataclass(frozen=True)
class FewShotCandidateContract:
    scope: RuntimeScope
    evidence_refs: tuple[EvidenceRef, ...]
    evidence_bindings: tuple[EvidenceBinding, ...]
    source_tags: tuple[dict[str, Any], ...]
    query_trace_id: str
    source_memory_id: int
    score: float
    traits: tuple[str, ...]


def _text(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise FewShotContractError(code)
    return normalized


def _source_tags(values: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise FewShotContractError("fewshot_source_tags_required")
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in values:
        if not isinstance(item, Mapping):
            raise FewShotContractError("fewshot_source_tag_invalid")
        try:
            tag_id = int(item.get("tag_id", item.get("id")))
            relevance = float(item.get("relevance") if item.get("relevance") is not None else 1.0)
            position = int(item.get("position") or 0)
        except (TypeError, ValueError) as exc:
            raise FewShotContractError("fewshot_source_tag_invalid") from exc
        name = _text(item.get("name"), "fewshot_source_tag_invalid")
        if tag_id <= 0 or position < 0 or not math.isfinite(relevance):
            raise FewShotContractError("fewshot_source_tag_invalid")
        if tag_id in seen:
            continue
        seen.add(tag_id)
        result.append({
            "tag_id": tag_id,
            "name": name,
            "tag_type": str(item.get("tag_type") or "keyword"),
            "position": position,
            "relevance": relevance,
            "source": str(item.get("source") or "automatic"),
        })
    if not result:
        raise FewShotContractError("fewshot_source_tags_required")
    return tuple(result)


def validate_fewshot_candidate_contract(
    candidate: Mapping[str, Any],
    *,
    bot_id: str,
    min_score: float = 0.7,
) -> FewShotCandidateContract:
    if not isinstance(candidate, Mapping) or str(candidate.get("candidate_type") or "") != FEWSHOT_CANDIDATE_TYPE:
        raise FewShotContractError("fewshot_candidate_type_required")
    evidence = candidate.get("evidence")
    if not isinstance(evidence, Mapping):
        raise FewShotContractError("fewshot_evidence_required")
    if str(evidence.get("contract_version") or "") != FEWSHOT_CONTRACT_VERSION:
        raise FewShotContractError("fewshot_contract_version_required")
    try:
        context = resolve_learning_promotion_scope(
            candidate,
            bot_id=bot_id,
            command_type="learning.few_shot_style.promote",
        )
    except LearningPromotionScopeError as exc:
        raise FewShotContractError(exc.reason_code, str(exc)) from exc

    query_trace_id = _text(evidence.get("query_trace_id"), "fewshot_query_trace_required")
    source_reply = evidence.get("source_reply")
    if not isinstance(source_reply, Mapping):
        raise FewShotContractError("fewshot_source_reply_required")
    try:
        source_memory_id = int(source_reply.get("memory_id"))
    except (TypeError, ValueError) as exc:
        raise FewShotContractError("fewshot_source_reply_invalid") from exc
    if source_memory_id <= 0:
        raise FewShotContractError("fewshot_source_reply_invalid")
    source_hash = _text(source_reply.get("content_hash"), "fewshot_source_reply_invalid")
    content_hash = "sha256:" + hashlib.sha256(
        str(candidate.get("content") or "").encode("utf-8")
    ).hexdigest()
    normalized_hash = str(source_reply.get("normalized_content_hash") or source_hash)
    if content_hash != normalized_hash:
        raise FewShotContractError("fewshot_reply_content_mismatch")

    refs_by_id = {item.id: item for item in context.evidence_refs}
    memory_ref = refs_by_id.get(f"memory:{source_memory_id}")
    trace_ref = refs_by_id.get(f"trace:{query_trace_id}")
    if memory_ref is None or memory_ref.kind != "bot_reply_memory" or memory_ref.content_hash != source_hash:
        raise FewShotContractError("fewshot_reply_evidence_required")
    if trace_ref is None or trace_ref.kind != "query_trace":
        raise FewShotContractError("fewshot_trace_evidence_required")
    binding_ids = {item.evidence_id for item in context.evidence_bindings}
    if memory_ref.id not in binding_ids or trace_ref.id not in binding_ids:
        raise FewShotContractError("fewshot_evidence_binding_required")
    if any(
        item.policy_version != FEWSHOT_BINDING_POLICY
        or item.derivation_chain != FEWSHOT_DERIVATION_CHAIN
        for item in context.evidence_bindings
    ):
        raise FewShotContractError("fewshot_evidence_binding_invalid")

    try:
        score = float(evidence.get("score"))
    except (TypeError, ValueError) as exc:
        raise FewShotContractError("fewshot_quality_score_required") from exc
    if not math.isfinite(score) or score < float(min_score) or score > 1.0:
        raise FewShotContractError("fewshot_quality_score_insufficient")
    raw_traits = evidence.get("traits")
    if isinstance(raw_traits, (str, bytes)) or not isinstance(raw_traits, Sequence):
        raise FewShotContractError("fewshot_traits_required")
    traits = tuple(dict.fromkeys(_text(value, "fewshot_traits_required") for value in raw_traits))
    if not traits:
        raise FewShotContractError("fewshot_traits_required")

    return FewShotCandidateContract(
        scope=context.scope,
        evidence_refs=context.evidence_refs,
        evidence_bindings=context.evidence_bindings,
        source_tags=_source_tags(evidence.get("source_tags")),
        query_trace_id=query_trace_id,
        source_memory_id=source_memory_id,
        score=score,
        traits=traits,
    )


__all__ = [
    "FEWSHOT_BINDING_POLICY",
    "FEWSHOT_CANDIDATE_TYPE",
    "FEWSHOT_CONTRACT_VERSION",
    "FEWSHOT_DERIVATION_CHAIN",
    "FewShotCandidateContract",
    "FewShotContractError",
    "validate_fewshot_candidate_contract",
]
