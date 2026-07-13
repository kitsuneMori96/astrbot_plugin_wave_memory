"""学习候选创建服务。"""

from __future__ import annotations

from typing import Any, Mapping

try:
    from ...domain.scope import UnresolvedScopeRef
    from ...engine.db.learning_repository import LearningRepositories
    from ..quality_gate import QualityGate, QualityGateError, decode_quality_evidence
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from domain.scope import UnresolvedScopeRef
    from engine.db.learning_repository import LearningRepositories
    from services.quality_gate import QualityGate, QualityGateError, decode_quality_evidence

from .book_experience import BOOK_EXPERIENCE_CANDIDATE, BookExperienceEvidenceValidator
from .scope_policy import require_learning_bot_id, resolve_learning_promotion_scope
from .source import LearningSourceItem

CORRECTION_LEARNING_CANDIDATE = "correction_learning"


class LearningCandidateService:
    """候选边界的唯一创建入口，不调用任何最终领域写入服务。"""

    def __init__(
        self,
        repositories: LearningRepositories,
        quality_gate: QualityGate | None = None,
    ):
        self.repositories = repositories
        self.quality_gate = quality_gate or QualityGate()

    @staticmethod
    def _require_bot_id(bot_id: str) -> str:
        return require_learning_bot_id(bot_id)

    def create(
        self,
        *,
        bot_id: str,
        candidate_type: str,
        content: str,
        evidence: dict[str, Any],
        source_fingerprint: str,
        source_id: int | None = None,
        job_id: int | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """创建或返回幂等候选；去重键由 repository 强制包含 bot_id。

        书中经历是高承诺候选，任何绕过来源适配器的创建也必须经过同一
        证据门禁；不在这里降级，避免调用方误以为仍创建了经历。
        """
        stable_bot_id = self._require_bot_id(bot_id)
        if str(candidate_type) == BOOK_EXPERIENCE_CANDIDATE:
            result = BookExperienceEvidenceValidator().validate(evidence)
            if not result.valid:
                detail = ", ".join(result.missing + result.errors) or "invalid evidence"
                raise ValueError(f"book_experience_episode evidence is insufficient: {detail}")
        if str(candidate_type) == CORRECTION_LEARNING_CANDIDATE:
            required = ("bot_reply", "user_correction", "message_ref", "book_lore_hits", "generated_text")
            missing = [field for field in required if field not in evidence or evidence[field] in (None, "")]
            message_ref = evidence.get("message_ref")
            if not isinstance(message_ref, dict) or not str(message_ref.get("group_id") or "").strip():
                missing.append("message_ref.group_id")
            if missing:
                raise ValueError("correction_learning evidence is insufficient: " + ", ".join(missing))
            # 纠正候选可能晋升为 memory/fact；在创建时即拒绝无 RuntimeScope、
            # 无可用 EvidenceRef 或跨 Scope 的 payload，避免把不可晋升候选留给审核页。
            resolve_learning_promotion_scope(
                {"evidence": evidence},
                bot_id=bot_id,
                command_type="learning.fact.promote",
            )
        target_scope, evidence_refs, evidence_bindings = decode_quality_evidence(evidence)
        raw_scope = (
            evidence_refs[0].source_scope
            if evidence_refs
            else target_scope
            or UnresolvedScopeRef(
                original_fields={"candidate_type": str(candidate_type)},
                reason_code="legacy_candidate_scope_absent",
                provenance={"source_fingerprint": str(source_fingerprint)},
            )
        )
        commitment = (
            "high"
            if evidence.get("commitment_level") == "high"
            or (isinstance(metadata, dict) and metadata.get("commitment_level") == "high")
            or str(candidate_type) == BOOK_EXPERIENCE_CANDIDATE
            else "low"
        )
        proposal = self.quality_gate.propose(
            operation="learning.candidate.create",
            content=str(content),
            raw_artifact=self.quality_gate.make_raw_artifact(
                kind="learning_candidate_source",
                artifact_id=str(source_fingerprint),
                content=str(content),
                source_scope=raw_scope,
                available=target_scope is not None,
            ),
            target_scope=target_scope,
            evidence_refs=evidence_refs,
            evidence_bindings=evidence_bindings,
            commitment=commitment,
            metadata={"bot_id": stable_bot_id, "candidate_type": str(candidate_type)},
        )
        decision = self.quality_gate.evaluate(proposal)
        if (commitment == "high" and not decision.allowed) or not decision.writable:
            raise QualityGateError(decision)
        quality_metadata = dict(metadata or {})
        quality_metadata["quality_decision"] = decision.to_dict()
        return self.repositories.candidates.create(
            bot_id=stable_bot_id,
            candidate_type=candidate_type,
            content=decision.normalized_content,
            evidence=evidence,
            source_fingerprint=source_fingerprint,
            source_id=source_id,
            job_id=job_id,
            reason=reason,
            metadata=quality_metadata,
        )

    create_candidate = create

    def create_from_item(
        self,
        item: LearningSourceItem | Mapping[str, Any],
        *,
        bot_id: str,
        candidate_type: str,
        source_id: int | None = None,
        job_id: int | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        normalized = LearningSourceItem.from_value(item)
        merged_metadata = dict(normalized.metadata)
        if metadata:
            merged_metadata.update(metadata)
        return self.create(
            bot_id=bot_id,
            candidate_type=candidate_type,
            content=normalized.content,
            evidence=normalized.evidence,
            source_fingerprint=normalized.source_fingerprint,
            source_id=source_id,
            job_id=job_id,
            reason=reason if reason is not None else normalized.reason,
            metadata=merged_metadata,
        )


__all__ = ["CORRECTION_LEARNING_CANDIDATE", "LearningCandidateService"]
