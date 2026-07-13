"""Learning promotion scope boundary.

This is a transitional boundary: it validates the canonical RuntimeScope and
EvidenceRef data before explicitly projecting an allowed group scope into the
legacy domain-service arguments.  It never derives scope from candidate text,
source names, or a default Bot value.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import logging
from typing import Any

try:
    from ...domain.evidence import EvidenceBinding, EvidenceRef
    from ...domain.scope import (
        RuntimeScope,
        ScopeValidationError,
        build_command_scope_policy_registry,
        scope_from_value,
    )
    from ..compat.scope_adapter import LegacyScopeAdapter
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from domain.evidence import EvidenceBinding, EvidenceRef
    from domain.scope import (
        RuntimeScope,
        ScopeValidationError,
        build_command_scope_policy_registry,
        scope_from_value,
    )
    from services.compat.scope_adapter import LegacyScopeAdapter

logger = logging.getLogger(__name__)


class LearningPromotionScopeError(ScopeValidationError):
    """A stable, terminal reason for a Learning promotion boundary failure."""


@dataclass(frozen=True)
class LearningPromotionScope:
    """Validated canonical scope plus its explicit temporary legacy projection."""

    scope: RuntimeScope
    group_id: str
    user_id: str | None
    policy_version: str


# 使用 domain 层的唯一命令 Scope Compatibility Matrix；Learning 只消费其中
# 已登记的 learning.* 命令，未知命令会被 registry fail closed。
_POLICY_REGISTRY = build_command_scope_policy_registry()

_LEGACY_PROJECTION_COUNTS: Counter[str] = Counter()


def _legacy_projection_warning(event: str, payload: Mapping[str, Any]) -> None:
    logger.warning(
        "[LearningScope] temporary legacy scope projection event=%s caller=%s outcome=%s",
        event,
        payload.get("caller", ""),
        payload.get("outcome", ""),
    )


def _legacy_projection_metric(event: str, payload: Mapping[str, Any]) -> None:
    _LEGACY_PROJECTION_COUNTS[
        f"{event}:{payload.get('caller', '')}:{payload.get('outcome', '')}"
    ] += 1


_LEGACY_SCOPE_ADAPTER = LegacyScopeAdapter(
    allowed_callers={
        "learning.fact.promote": frozenset({"group"}),
        "learning.worldview_internalization.promote": frozenset({"group"}),
        "learning.book_experience.promote": frozenset({"group"}),
        "learning.few_shot_style.promote": frozenset({"group"}),
        "learning.relationship.promote": frozenset({"group"}),
        "learning.book_lore.promote": frozenset({"group"}),
        "learning.memory.promote": frozenset({"group"}),
    },
    warning_hook=_legacy_projection_warning,
    metric_hook=_legacy_projection_metric,
)


def legacy_scope_projection_metrics() -> dict[str, int]:
    """Return a snapshot of temporary legacy projections for observability/tests."""
    return dict(_LEGACY_PROJECTION_COUNTS)


def require_learning_bot_id(bot_id: str) -> str:
    """Reject values that cannot be a stable BotProfile.db_id."""
    normalized = str(bot_id or "").strip()
    if not normalized or normalized.isdecimal() or normalized.casefold() in {"bot", "default"}:
        raise LearningPromotionScopeError(
            "invalid_bot_db_id",
            "bot_id must be a non-default, non-numeric BotProfile.db_id",
        )
    return normalized


def resolve_learning_promotion_scope(
    candidate: Mapping[str, Any],
    *,
    bot_id: str,
    command_type: str,
) -> LearningPromotionScope:
    """Validate target scope and evidence before an allowed legacy projection.

    The current legacy Fact and Worldview methods retain only a group_id field,
    so this migration slice deliberately supports real group RuntimeScope only.
    It rejects private/bot-private values rather than manufacturing pseudo group
    IDs; those forms require their domain schema migration first.
    """
    stable_bot_id = require_learning_bot_id(bot_id)
    evidence = candidate.get("evidence") if isinstance(candidate, Mapping) else None
    if not isinstance(evidence, Mapping):
        raise LearningPromotionScopeError("scope_required", "candidate evidence must declare target scope")

    scope = _target_scope(evidence)
    decision = _POLICY_REGISTRY.validate(command_type, scope)
    if not decision.allowed:
        raise LearningPromotionScopeError(decision.reason_code or "scope_rejected", "target scope is not accepted")
    if scope.bot_id != stable_bot_id:
        raise LearningPromotionScopeError("scope_bot_mismatch", "target scope Bot does not match promotion Bot")

    _validate_evidence_bindings(evidence, scope)
    _validate_high_commitment_anchor(evidence, scope)

    projected = _LEGACY_SCOPE_ADAPTER.project_runtime(
        scope,
        caller=command_type,
        target="group",
        require_subject=command_type == "learning.fact.promote",
    )
    if projected.group_id is None:
        raise LearningPromotionScopeError("scope_visibility_not_allowed", "legacy projection requires a real group")

    declared_group = evidence.get("group_id")
    if declared_group is not None and str(declared_group).strip() != projected.group_id:
        raise LearningPromotionScopeError(
            "scope_session_mismatch",
            "candidate group_id does not match the canonical RuntimeScope session",
        )
    return LearningPromotionScope(
        scope=scope,
        group_id=projected.group_id,
        user_id=projected.user_id,
        policy_version=decision.policy_version,
    )


def _target_scope(evidence: Mapping[str, Any]) -> RuntimeScope:
    raw_scope = evidence.get("scope", evidence.get("target_scope"))
    if raw_scope is None:
        raise LearningPromotionScopeError("scope_required", "candidate evidence must include a target RuntimeScope")
    try:
        scope = scope_from_value(raw_scope)
    except ScopeValidationError as exc:
        code = "invalid_bot_db_id" if exc.reason_code == "invalid_bot_id" else exc.reason_code
        raise LearningPromotionScopeError(code, str(exc)) from exc
    if not isinstance(scope, RuntimeScope):
        raise LearningPromotionScopeError("scope_type_not_allowed", "Learning promotion requires RuntimeScope")

    alternate = evidence.get("target_scope")
    if alternate is not None:
        try:
            alternate_scope = scope_from_value(alternate)
        except ScopeValidationError as exc:
            raise LearningPromotionScopeError(exc.reason_code, str(exc)) from exc
        if alternate_scope != scope:
            raise LearningPromotionScopeError("scope_target_mismatch", "scope and target_scope must agree")
    return scope


def _validate_evidence_bindings(evidence: Mapping[str, Any], target_scope: RuntimeScope) -> None:
    refs = _decode_many(evidence, plural="evidence_refs", singular="evidence_ref", decoder=EvidenceRef.from_dict)
    if not refs:
        raise LearningPromotionScopeError("evidence_required", "promotion requires at least one EvidenceRef")
    ref_ids: set[str] = set()
    for ref in refs:
        if not ref.available:
            raise LearningPromotionScopeError("evidence_unavailable", "promotion evidence is unavailable")
        if ref.source_scope != target_scope:
            raise LearningPromotionScopeError(
                "evidence_scope_mismatch",
                "Runtime promotion evidence must have the identical target RuntimeScope",
            )
        ref_ids.add(ref.id)

    bindings = _decode_many(
        evidence,
        plural="evidence_bindings",
        singular="evidence_binding",
        decoder=EvidenceBinding.from_dict,
    )
    if not bindings:
        raise LearningPromotionScopeError("evidence_binding_required", "promotion requires an EvidenceBinding")
    for binding in bindings:
        if binding.evidence_id not in ref_ids:
            raise LearningPromotionScopeError(
                "evidence_binding_mismatch",
                "EvidenceBinding must reference a supplied EvidenceRef",
            )
        if binding.target_scope != target_scope:
            raise LearningPromotionScopeError(
                "evidence_scope_mismatch",
                "EvidenceBinding target must equal the promotion RuntimeScope",
            )


def _validate_high_commitment_anchor(evidence: Mapping[str, Any], target_scope: RuntimeScope) -> None:
    if evidence.get("commitment_level") != "high":
        return
    raw_anchor = evidence.get("anchor_ref")
    if raw_anchor is None:
        raise LearningPromotionScopeError("anchor_required", "high-commitment promotion requires anchor_ref")
    try:
        anchor = EvidenceRef.from_dict(raw_anchor)
    except ScopeValidationError as exc:
        raise LearningPromotionScopeError(exc.reason_code, str(exc)) from exc
    if not anchor.available:
        raise LearningPromotionScopeError("evidence_unavailable", "anchor evidence is unavailable")
    if anchor.source_scope != target_scope:
        raise LearningPromotionScopeError("evidence_scope_mismatch", "anchor scope must equal target RuntimeScope")


def _decode_many(
    evidence: Mapping[str, Any],
    *,
    plural: str,
    singular: str,
    decoder,
) -> tuple[Any, ...]:
    raw_items: list[Any] = []
    plural_value = evidence.get(plural)
    if plural_value is not None:
        if isinstance(plural_value, (str, bytes)) or not isinstance(plural_value, Sequence):
            raise LearningPromotionScopeError("invalid_evidence_shape", f"{plural} must be a sequence")
        raw_items.extend(plural_value)
    singular_value = evidence.get(singular)
    if singular_value is not None:
        raw_items.append(singular_value)

    decoded: list[Any] = []
    for raw_item in raw_items:
        try:
            decoded.append(decoder(raw_item))
        except ScopeValidationError as exc:
            raise LearningPromotionScopeError(exc.reason_code, str(exc)) from exc
    return tuple(decoded)


__all__ = [
    "LearningPromotionScope",
    "LearningPromotionScopeError",
    "legacy_scope_projection_metrics",
    "require_learning_bot_id",
    "resolve_learning_promotion_scope",
]
