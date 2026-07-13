"""Immutable evidence references and explicit Catalog-to-Runtime derivations."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, TypeAlias

from .scope import (
    CatalogScope,
    RuntimeScope,
    ScopeRef,
    ScopeValidationError,
    UnresolvedScopeRef,
    scope_from_value,
)

Provenance: TypeAlias = Mapping[str, Any]
FULL_EVIDENCE_DERIVATION_CHAIN = (
    "raw",
    "reviewed_projection",
    "scoped_candidate",
    "domain_object",
)


@dataclass(frozen=True)
class RawArtifactRef:
    """Immutable pointer to the raw input inspected by the quality boundary.

    A raw artifact is deliberately not an ``EvidenceRef``: it proves which bytes were
    inspected, but only reviewed/available evidence may support a formal domain write.
    Keeping the same scope vocabulary makes that distinction explicit without inventing
    a second scope model.
    """

    kind: str
    id: str
    content_hash: str
    captured_at: float
    source_scope: ScopeRef
    available: bool = True

    def __post_init__(self) -> None:
        _require_string(self.kind, "raw_artifact_kind_required", "kind")
        _require_string(self.id, "raw_artifact_id_required", "id")
        _require_string(self.content_hash, "content_hash_required", "content_hash")
        if isinstance(self.captured_at, bool) or not isinstance(self.captured_at, (int, float)):
            raise ScopeValidationError("invalid_captured_at", "captured_at must be a finite timestamp")
        if not math.isfinite(float(self.captured_at)) or float(self.captured_at) < 0:
            raise ScopeValidationError("invalid_captured_at", "captured_at must be a finite timestamp")
        if not isinstance(self.source_scope, (RuntimeScope, CatalogScope, UnresolvedScopeRef)):
            raise ScopeValidationError("invalid_evidence_scope", "source_scope must be a ScopeRef")
        if not isinstance(self.available, bool):
            raise ScopeValidationError("invalid_evidence_availability", "available must be bool")
        if isinstance(self.source_scope, UnresolvedScopeRef) and self.available:
            raise ScopeValidationError(
                "unresolved_raw_artifact_available",
                "an unresolved raw artifact cannot be marked available",
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RawArtifactRef":
        if not isinstance(value, Mapping):
            raise ScopeValidationError("invalid_raw_artifact_ref", "raw artifact reference must be a mapping")
        try:
            source_scope = scope_from_value(value["source_scope"])
        except KeyError as exc:
            raise ScopeValidationError("evidence_scope_required", "raw artifact source_scope is required") from exc
        return cls(
            kind=value.get("kind"),
            id=value.get("id"),
            content_hash=value.get("content_hash"),
            captured_at=value.get("captured_at"),
            source_scope=source_scope,
            available=value.get("available", True),
        )

    def as_evidence_ref(self, *, available: bool | None = None) -> "EvidenceRef":
        """Promote the pointer only when a caller explicitly creates reviewed evidence."""
        return EvidenceRef(
            kind=self.kind,
            id=self.id,
            content_hash=self.content_hash,
            captured_at=self.captured_at,
            source_scope=self.source_scope,
            available=self.available if available is None else available,
        )


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    id: str
    content_hash: str
    captured_at: float
    source_scope: ScopeRef
    available: bool

    def __post_init__(self) -> None:
        _require_string(self.kind, "evidence_kind_required", "kind")
        _require_string(self.id, "evidence_id_required", "id")
        _require_string(self.content_hash, "content_hash_required", "content_hash")
        if isinstance(self.captured_at, bool) or not isinstance(self.captured_at, (int, float)):
            raise ScopeValidationError("invalid_captured_at", "captured_at must be a finite timestamp")
        if not math.isfinite(float(self.captured_at)) or float(self.captured_at) < 0:
            raise ScopeValidationError("invalid_captured_at", "captured_at must be a finite timestamp")
        if not isinstance(self.source_scope, (RuntimeScope, CatalogScope, UnresolvedScopeRef)):
            raise ScopeValidationError("invalid_evidence_scope", "source_scope must be a ScopeRef")
        if not isinstance(self.available, bool):
            raise ScopeValidationError("invalid_evidence_availability", "available must be bool")
        if isinstance(self.source_scope, UnresolvedScopeRef) and self.available:
            raise ScopeValidationError(
                "unresolved_evidence_available",
                "unresolved evidence cannot be marked available for formal use",
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceRef":
        if not isinstance(value, Mapping):
            raise ScopeValidationError("invalid_evidence_ref", "evidence reference must be a mapping")
        try:
            source_scope = scope_from_value(value["source_scope"])
        except KeyError as exc:
            raise ScopeValidationError("evidence_scope_required", "evidence source_scope is required") from exc
        return cls(
            kind=value.get("kind"),
            id=value.get("id"),
            content_hash=value.get("content_hash"),
            captured_at=value.get("captured_at"),
            source_scope=source_scope,
            available=value.get("available"),
        )


@dataclass(frozen=True)
class EvidenceBinding:
    evidence_id: str
    target_scope: RuntimeScope | CatalogScope
    derivation_chain: tuple[str, ...]
    policy_version: str

    def __post_init__(self) -> None:
        _require_string(self.evidence_id, "evidence_id_required", "evidence_id")
        if not isinstance(self.target_scope, (RuntimeScope, CatalogScope)):
            raise ScopeValidationError("invalid_target_scope", "target_scope must be resolved")
        if not isinstance(self.derivation_chain, tuple) or not self.derivation_chain:
            raise ScopeValidationError("invalid_derivation_chain", "derivation_chain must be a non-empty tuple")
        if any(not isinstance(stage, str) or not stage for stage in self.derivation_chain):
            raise ScopeValidationError("invalid_derivation_chain", "derivation stages must be non-empty strings")
        _require_string(self.policy_version, "derivation_policy_required", "policy_version")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvidenceBinding":
        if not isinstance(value, Mapping):
            raise ScopeValidationError("invalid_evidence_binding", "evidence binding must be a mapping")
        try:
            target_scope = scope_from_value(value["target_scope"])
        except KeyError as exc:
            raise ScopeValidationError("target_scope_required", "evidence binding target_scope is required") from exc
        if not isinstance(target_scope, (RuntimeScope, CatalogScope)):
            raise ScopeValidationError("invalid_target_scope", "evidence binding target_scope must be resolved")
        derivation_chain = value.get("derivation_chain")
        if isinstance(derivation_chain, list):
            derivation_chain = tuple(derivation_chain)
        return cls(
            evidence_id=value.get("evidence_id"),
            target_scope=target_scope,
            derivation_chain=derivation_chain,
            policy_version=value.get("policy_version"),
        )


@dataclass(frozen=True)
class EvidenceDerivation:
    kind: str
    reviewed: bool
    review_status: str
    derivation_version: str
    policy_version: str
    source: CatalogScope
    target: RuntimeScope
    derivation_chain: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind != "EvidenceDerivation":
            raise ScopeValidationError("invalid_derivation_kind", "kind must be EvidenceDerivation")
        if self.reviewed is not True or self.review_status != "reviewed":
            raise ScopeValidationError(
                "derivation_not_reviewed",
                "Catalog-to-Runtime derivation must be explicitly reviewed",
            )
        _require_string(
            self.derivation_version,
            "derivation_version_required",
            "derivation_version",
        )
        _require_string(self.policy_version, "derivation_policy_required", "policy_version")
        if not isinstance(self.source, CatalogScope):
            raise ScopeValidationError("catalog_scope_required", "source must be CatalogScope")
        if not isinstance(self.target, RuntimeScope):
            raise ScopeValidationError("runtime_scope_required", "target must be RuntimeScope")
        if self.derivation_chain != FULL_EVIDENCE_DERIVATION_CHAIN:
            raise ScopeValidationError(
                "invalid_derivation_chain",
                "derivation must be raw -> reviewed_projection -> scoped_candidate -> domain_object",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reviewed": self.reviewed,
            "review_status": self.review_status,
            "derivation_version": self.derivation_version,
            "policy_version": self.policy_version,
            "source": asdict(self.source),
            "target": asdict(self.target),
            "derivation_chain": self.derivation_chain,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceDerivation":
        expected = (
            "kind",
            "reviewed",
            "review_status",
            "derivation_version",
            "policy_version",
            "source",
            "target",
            "derivation_chain",
        )
        if not isinstance(payload, Mapping) or set(payload) != set(expected):
            raise ScopeValidationError(
                "invalid_evidence_derivation",
                f"EvidenceDerivation fields must be exactly {expected!r}",
            )
        source = payload["source"]
        target = payload["target"]
        return cls(
            kind=payload["kind"],
            reviewed=payload["reviewed"],
            review_status=payload["review_status"],
            derivation_version=payload["derivation_version"],
            policy_version=payload["policy_version"],
            source=source if isinstance(source, CatalogScope) else CatalogScope.from_dict(source),
            target=target if isinstance(target, RuntimeScope) else RuntimeScope.from_dict(target),
            derivation_chain=tuple(payload["derivation_chain"]),
        )


def _require_string(value: Any, reason_code: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ScopeValidationError(reason_code, f"{field_name} must be a non-empty exact string")
    return value


__all__ = [
    "EvidenceBinding",
    "EvidenceDerivation",
    "EvidenceRef",
    "FULL_EVIDENCE_DERIVATION_CHAIN",
    "Provenance",
    "RawArtifactRef",
]
