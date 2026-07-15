"""Versioned, transaction-external quality gate for proposed writes."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any, Callable

try:
    from ..domain.evidence import (
        EvidenceBinding,
        EvidenceRef,
        FULL_EVIDENCE_DERIVATION_CHAIN,
        RawArtifactRef,
    )
    from ..domain.quality import QualityDecision, QualityProposal
    from ..domain.scope import (
        CatalogScope,
        RuntimeScope,
        ScopeRef,
        ScopeValidationError,
        UnresolvedScopeRef,
        scope_from_value,
    )
    from .identity_safety import is_identity_contamination
except ImportError:  # pragma: no cover - standalone services imports
    from domain.evidence import (
        EvidenceBinding,
        EvidenceRef,
        FULL_EVIDENCE_DERIVATION_CHAIN,
        RawArtifactRef,
    )
    from domain.quality import QualityDecision, QualityProposal
    from domain.scope import (
        CatalogScope,
        RuntimeScope,
        ScopeRef,
        ScopeValidationError,
        UnresolvedScopeRef,
        scope_from_value,
    )
    from services.identity_safety import is_identity_contamination

_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
_SYSTEM_INSTRUCTION_RE = re.compile(
    r"(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system)\s+(?:instructions?|prompts?)"
    r"|(?:system|developer)\s*(?:prompt|message|instruction)\s*:"
    r"|<\/?(?:system|developer|assistant)(?:\s|>)"
    r"|(?:忽略|无视|覆盖|绕过).{0,12}(?:系统|开发者|先前|以上).{0,8}(?:指令|提示|规则)"
    r"|(?:系统|开发者)(?:指令|提示词)\s*[:：]",
    re.IGNORECASE,
)
_MOJIBAKE_MARKERS = ("\ufffd", "Ã", "Â", "â€", "ðŸ", "锟斤拷", "烫烫烫", "屯屯屯")


class QualityGateError(ValueError):
    """A non-writable decision with a stable machine-readable reason code."""

    def __init__(self, decision: QualityDecision):
        self.decision = decision
        self.reason_code = decision.reason_code
        self.code = decision.reason_code
        self.outcome = decision.outcome
        self.rule_version = decision.rule_version
        super().__init__(f"{decision.reason_code}: quality outcome={decision.outcome}")


class QualityGate:
    RULE_VERSION = "quality-gate/v1"
    rule_version = RULE_VERSION

    def __init__(self, repository: Any | None = None, *, now: Callable[[], float] | None = None):
        self.repository = repository
        self.now = now or time.time

    def make_raw_artifact(
        self,
        *,
        kind: str,
        artifact_id: str,
        content: str,
        source_scope: ScopeRef,
        captured_at: float | None = None,
        available: bool = True,
    ) -> RawArtifactRef:
        digest = "sha256:" + hashlib.sha256(str(content).encode("utf-8")).hexdigest()
        return RawArtifactRef(
            kind=kind,
            id=artifact_id,
            content_hash=digest,
            captured_at=float(self.now() if captured_at is None else captured_at),
            source_scope=source_scope,
            available=available,
        )

    def propose(
        self,
        *,
        operation: str,
        content: str,
        raw_artifact: RawArtifactRef,
        target_scope: RuntimeScope | CatalogScope | None = None,
        evidence_refs: Sequence[EvidenceRef] = (),
        evidence_bindings: Sequence[EvidenceBinding] = (),
        commitment: str = "low",
        metadata: Mapping[str, Any] | None = None,
        proposal_id: str | None = None,
    ) -> QualityProposal:
        if proposal_id is None:
            scope_seed = (
                "none"
                if target_scope is None
                else json.dumps(
                    target_scope.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            seed = "\x1f".join(
                (operation, raw_artifact.id, raw_artifact.content_hash, commitment, scope_seed)
            )
            proposal_id = "quality:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return QualityProposal(
            proposal_id=proposal_id,
            operation=operation,
            content=content,
            raw_artifact=raw_artifact,
            target_scope=target_scope,
            evidence_refs=tuple(evidence_refs),
            evidence_bindings=tuple(evidence_bindings),
            commitment=commitment,  # type: ignore[arg-type]
            metadata=metadata or {},
        )

    def evaluate(self, proposal: QualityProposal, *, record: bool = True) -> QualityDecision:
        if not isinstance(proposal, QualityProposal):
            raise TypeError("QualityGate requires a QualityProposal")

        normalized = unicodedata.normalize("NFKC", proposal.content)
        has_zero_width = bool(_ZERO_WIDTH_RE.search(normalized))
        normalized = _ZERO_WIDTH_RE.sub("", normalized)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()

        outcome, reasons = self._structural_decision(proposal)
        if outcome is None:
            outcome, reasons = self._content_decision(
                proposal.content,
                normalized,
                has_zero_width=has_zero_width,
            )
        if outcome is None:
            reasons = ["quality_allowed"]
            if normalized != proposal.content.strip():
                reasons.append("text_normalized_nfkc")
            outcome = "allow"

        decision = QualityDecision(
            proposal_id=proposal.proposal_id,
            outcome=outcome,  # type: ignore[arg-type]
            reason_code=reasons[0],
            reason_codes=tuple(reasons),
            rule_version=self.RULE_VERSION,
            normalized_content=normalized,
        )
        if record and self.repository is not None:
            self.repository.record(proposal, decision)
        return decision

    decide = evaluate

    def enforce(self, proposal: QualityProposal) -> QualityDecision:
        decision = self.evaluate(proposal)
        if not decision.writable:
            raise QualityGateError(decision)
        return decision

    require_writable = enforce

    def _structural_decision(self, proposal: QualityProposal) -> tuple[str | None, list[str]]:
        raw_hash = str(proposal.raw_artifact.content_hash or "")
        if not raw_hash.startswith("sha256:"):
            return "reject", ["raw_artifact_hash_invalid"]
        # Message ingestion constructs RawArtifactRef from the exact content that
        # is about to be written. Reject a forged hash instead of treating a
        # caller-supplied artifact token as evidence of the bytes inspected.
        if proposal.raw_artifact.kind == "chat_message":
            expected_hash = "sha256:" + hashlib.sha256(proposal.content.encode("utf-8")).hexdigest()
            if raw_hash != expected_hash:
                return "reject", ["raw_artifact_hash_mismatch"]
        if proposal.target_scope is None:
            if proposal.evidence_refs or proposal.evidence_bindings:
                return "reject", ["scope_required"]
            if proposal.commitment == "high":
                return "defer", ["scope_required"]
            return None, []

        refs = proposal.evidence_refs
        bindings = proposal.evidence_bindings
        if proposal.commitment == "high" and not refs:
            return "defer", ["evidence_required"]
        if any(not ref.available for ref in refs):
            return "defer", ["evidence_unavailable"]
        if proposal.commitment == "high" and not bindings:
            return "defer", ["evidence_binding_required"]

        ref_ids = {ref.id for ref in refs}
        binding_by_evidence = {binding.evidence_id: binding for binding in bindings}
        for binding in bindings:
            if binding.evidence_id not in ref_ids:
                return "reject", ["evidence_binding_mismatch"]
            if binding.target_scope != proposal.target_scope:
                return "reject", ["evidence_scope_mismatch"]
        for ref in refs:
            if ref.source_scope == proposal.target_scope:
                continue
            binding = binding_by_evidence.get(ref.id)
            is_catalog_projection = (
                isinstance(ref.source_scope, CatalogScope)
                and isinstance(proposal.target_scope, RuntimeScope)
                and binding is not None
            )
            if is_catalog_projection and binding.policy_version not in {
                "scope-derivation/v1",
                "book-experience/v1",
                "worldview-internalization/v1",
            }:
                return "reject", ["evidence_policy_unsupported"]
            catalog_projection = (
                is_catalog_projection
                and binding.derivation_chain == FULL_EVIDENCE_DERIVATION_CHAIN
            )
            if not catalog_projection:
                return "reject", ["evidence_scope_mismatch"]
        return None, []

    @staticmethod
    def _content_decision(
        original: str,
        normalized: str,
        *,
        has_zero_width: bool,
    ) -> tuple[str | None, list[str]]:
        if not normalized:
            return "reject", ["content_empty_after_normalization"]

        replacement_count = original.count("\ufffd")
        control_count = sum(
            1
            for char in original
            if unicodedata.category(char) == "Cc" and char not in "\n\r\t"
        )
        marker_count = sum(original.count(marker) for marker in _MOJIBAKE_MARKERS)
        if replacement_count >= 2 or control_count >= 2:
            return "reject", ["text_garbled"]
        if replacement_count or control_count or marker_count >= 2:
            return "quarantine", ["text_garbled"]
        if has_zero_width:
            return "quarantine", ["zero_width_characters"]
        if _SYSTEM_INSTRUCTION_RE.search(normalized):
            return "quarantine", ["system_instruction_contamination"]
        if is_identity_contamination(normalized):
            return "quarantine", ["identity_contamination"]
        return None, []


def decode_quality_evidence(
    payload: Mapping[str, Any] | None,
) -> tuple[RuntimeScope | CatalogScope | None, tuple[EvidenceRef, ...], tuple[EvidenceBinding, ...]]:
    """Decode canonical scope/evidence fields without guessing from legacy IDs."""
    if not isinstance(payload, Mapping):
        return None, (), ()
    raw_scope = payload.get("target_scope", payload.get("scope"))
    target_scope = None
    if raw_scope is not None:
        scope = scope_from_value(raw_scope)
        if isinstance(scope, UnresolvedScopeRef):
            raise ScopeValidationError("unresolved_scope", "quality target scope must be resolved")
        target_scope = scope

    refs = _decode_many(payload, "evidence_refs", "evidence_ref", EvidenceRef)
    bindings = _decode_many(payload, "evidence_bindings", "evidence_binding", EvidenceBinding)
    return target_scope, refs, bindings


def _decode_many(payload: Mapping[str, Any], plural: str, singular: str, cls: Any) -> tuple[Any, ...]:
    values: list[Any] = []
    many = payload.get(plural)
    if many is not None:
        if isinstance(many, (str, bytes)) or not isinstance(many, Sequence):
            raise ScopeValidationError("invalid_evidence_shape", f"{plural} must be a sequence")
        values.extend(many)
    one = payload.get(singular)
    if one is not None:
        values.append(one)
    decoded: list[Any] = []
    for value in values:
        decoded.append(value if isinstance(value, cls) else cls.from_dict(value))
    # Singular and plural compatibility fields often point to the same object.
    return tuple({item.id if isinstance(item, EvidenceRef) else (item.evidence_id, item.target_scope): item for item in decoded}.values())


__all__ = ["QualityGate", "QualityGateError", "decode_quality_evidence"]
