"""Immutable quality-gate proposal and decision contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, TypeAlias

from .evidence import EvidenceBinding, EvidenceRef, RawArtifactRef
from .scope import CatalogScope, RuntimeScope

QualityOutcome: TypeAlias = Literal["allow", "quarantine", "reject", "defer"]
CommitmentLevel: TypeAlias = Literal["low", "high"]
_OUTCOMES = frozenset({"allow", "quarantine", "reject", "defer"})


def _exact_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty exact string")
    return value


@dataclass(frozen=True)
class QualityProposal:
    """Transaction-free input shared by every quality-gated write entry.

    Creating/evaluating a proposal never opens the final writer transaction.  The
    caller may persist the decision for audit and only then delegate to its existing
    writer/repository.
    """

    proposal_id: str
    operation: str
    content: str
    raw_artifact: RawArtifactRef
    target_scope: RuntimeScope | CatalogScope | None = None
    evidence_refs: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    evidence_bindings: tuple[EvidenceBinding, ...] = field(default_factory=tuple)
    commitment: CommitmentLevel = "low"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _exact_text(self.proposal_id, "proposal_id")
        _exact_text(self.operation, "operation")
        if not isinstance(self.content, str):
            raise TypeError("content must be str")
        if not isinstance(self.raw_artifact, RawArtifactRef):
            raise TypeError("raw_artifact must be RawArtifactRef")
        if self.target_scope is not None and not isinstance(self.target_scope, (RuntimeScope, CatalogScope)):
            raise TypeError("target_scope must be a resolved scope or None")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(item, EvidenceRef) for item in self.evidence_refs
        ):
            raise TypeError("evidence_refs must be a tuple of EvidenceRef")
        if not isinstance(self.evidence_bindings, tuple) or any(
            not isinstance(item, EvidenceBinding) for item in self.evidence_bindings
        ):
            raise TypeError("evidence_bindings must be a tuple of EvidenceBinding")
        if self.commitment not in {"low", "high"}:
            raise ValueError("commitment must be low or high")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def raw_artifact_ref(self) -> RawArtifactRef:
        return self.raw_artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "operation": self.operation,
            "content": self.content,
            "raw_artifact": self.raw_artifact.to_dict(),
            "target_scope": None if self.target_scope is None else asdict(self.target_scope),
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "evidence_bindings": [item.to_dict() for item in self.evidence_bindings],
            "commitment": self.commitment,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class QualityDecision:
    """Stable, serializable result of one versioned quality ruleset."""

    proposal_id: str
    outcome: QualityOutcome
    reason_code: str
    rule_version: str
    normalized_content: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _exact_text(self.proposal_id, "proposal_id")
        if self.outcome not in _OUTCOMES:
            raise ValueError(f"unsupported quality outcome: {self.outcome!r}")
        _exact_text(self.reason_code, "reason_code")
        _exact_text(self.rule_version, "rule_version")
        if not isinstance(self.normalized_content, str):
            raise TypeError("normalized_content must be str")
        codes = self.reason_codes or (self.reason_code,)
        if any(not isinstance(code, str) or not code or code != code.strip() for code in codes):
            raise ValueError("reason_codes must contain non-empty exact strings")
        if codes[0] != self.reason_code:
            codes = (self.reason_code, *(code for code in codes if code != self.reason_code))
        object.__setattr__(self, "reason_codes", tuple(dict.fromkeys(codes)))

    @property
    def action(self) -> QualityOutcome:
        return self.outcome

    @property
    def disposition(self) -> QualityOutcome:
        return self.outcome

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"

    @property
    def writable(self) -> bool:
        return self.outcome in {"allow", "quarantine"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "reason_codes": self.reason_codes,
            "rule_version": self.rule_version,
            "normalized_content": self.normalized_content,
        }


__all__ = [
    "CommitmentLevel",
    "QualityDecision",
    "QualityOutcome",
    "QualityProposal",
]
