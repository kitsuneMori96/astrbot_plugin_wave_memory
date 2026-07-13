"""R2 RED contracts aligned exactly with frozen Design section 3.1 scope types."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict, fields
from typing import Any, Mapping

from tests.system_convergence.contracts import (
    contract_assert,
    load_scope_validation_adapter,
    reason_code,
    strict_allowed,
)


def _roundtrip(value, world, reason: str):
    payload = asdict(value)
    if isinstance(value, world.RuntimeScope) and payload["session"] is not None:
        payload["session"] = world.SessionRef(**payload["session"])
    restored = type(value)(**payload)
    contract_assert(restored == value, reason, "dataclasses.asdict roundtrip changed identity")
    contract_assert(asdict(restored) == asdict(value), reason, "roundtrip changed public fields")
    return restored


def _identity(value: Any, world, reason: str):
    if isinstance(value, world.SessionRef):
        return (value.id, value.platform_id, value.kind, value.conversation_id)
    if isinstance(value, world.RuntimeScope):
        session = _identity(value.session, world, reason) if value.session is not None else None
        return (value.bot_id, value.visibility, session, value.subject_principal_id)
    if isinstance(value, world.CatalogScope):
        return (value.catalog_id, value.corpus_id, value.version)
    if isinstance(value, world.UnresolvedScopeRef):
        original = tuple(sorted(dict(value.original_fields).items()))
        provenance = tuple(sorted(dict(value.provenance).items()))
        return (original, value.reason_code, provenance)
    contract_assert(False, reason, f"unknown ScopeRef type {type(value)!r}")


def _assert_delegated(adapter, validator, previous_count: int, reason: str) -> None:
    contract_assert(
        len(adapter.calls) == previous_count + 1,
        reason,
        "scope adapter did not record exactly one validator delegation",
    )
    contract_assert(
        adapter.calls[-1].validator_id == id(validator),
        reason,
        "scope adapter did not call the supplied ScopeValidator instance",
    )


def _allowed(call, reason: str) -> bool:
    try:
        result = call()
    except Exception as exc:
        contract_assert(
            reason_code(exc) is not None,
            reason,
            f"validator rejection lacks stable reason code: {type(exc).__name__}: {exc}",
        )
        return False
    return strict_allowed(result, reason)


def _assert_frozen(value, reason: str) -> None:
    field_name = fields(value)[0].name
    original = getattr(value, field_name)
    replacement = "mutated" if not isinstance(original, Mapping) else {"mutated": True}
    try:
        setattr(value, field_name, replacement)
    except (FrozenInstanceError, AttributeError, TypeError):
        return
    contract_assert(False, reason, f"{type(value).__name__}.{field_name} is mutable")


def test_frozen_design_scope_fields_are_immutable_serializable_and_collision_free(
    scope_world_factory,
):
    reason = "R2_SCOPE_IDENTITY_EXECUTABLE"
    world = scope_world_factory(reason)
    values = (
        world.group_a,
        world.group_b,
        world.private,
        world.runtime_alpha_group_a,
        world.runtime_beta_group_a,
        world.runtime_alpha_group_b,
        world.runtime_alpha_private,
        world.runtime_alpha_bot_private,
        world.catalog,
        world.unresolved,
    )
    for value in values:
        restored = _roundtrip(value, world, reason)
        contract_assert(
            _identity(restored, world, reason) == _identity(value, world, reason),
            reason,
            "public-field identity changed after serialization",
        )
        _assert_frozen(value, reason)

    identities = {
        _identity(world.runtime_alpha_group_a, world, reason),
        _identity(world.runtime_beta_group_a, world, reason),
        _identity(world.runtime_alpha_group_b, world, reason),
        _identity(world.runtime_alpha_private, world, reason),
        _identity(world.runtime_alpha_bot_private, world, reason),
    }
    contract_assert(len(identities) == 5, reason, "distinct RuntimeScope public fields collided")
    contract_assert(
        world.runtime_alpha_group_a.subject_principal_id == "qq:user:900000001",
        reason,
        "subject principal is not carried by RuntimeScope",
    )


def test_session_requirements_and_unresolved_legacy_are_fail_closed(scope_world_factory):
    reason = "R2_SCOPE_VALIDATOR_FAIL_CLOSED"
    world = scope_world_factory(reason)
    contract_assert(
        world.runtime_alpha_bot_private.session is None,
        reason,
        "bot_private RuntimeScope retained a session",
    )
    for visibility in ("group", "private"):
        try:
            world.RuntimeScope(
                bot_id="bot-alpha",
                visibility=visibility,
                session=None,
                subject_principal_id="qq:user:900000001",
            )
        except Exception as exc:
            contract_assert(
                reason_code(exc) == "session_required",
                reason,
                "missing session has unstable reason code",
            )
        else:
            contract_assert(False, reason, f"{visibility} RuntimeScope accepted no session")

    _roundtrip(world.unresolved, world, reason)
    adapter = load_scope_validation_adapter(world.validator, reason)
    for purpose in ("domain", "query", "injection"):
        previous_count = len(adapter.calls)
        try:
            adapter.validate_unresolved_target(scope=world.unresolved, purpose=purpose)
        except Exception as exc:
            contract_assert(
                reason_code(exc) == "unresolved_scope",
                reason,
                "unresolved target rejection has unstable reason code",
            )
        else:
            contract_assert(False, reason, f"UnresolvedScopeRef was accepted for {purpose}")
        _assert_delegated(adapter, world.validator, previous_count, reason)


def test_runtime_compatibility_and_reviewed_catalog_derivation_use_real_validator(
    scope_world_factory,
):
    reason = "R2_SCOPE_COMPATIBILITY_EXECUTABLE"
    world = scope_world_factory(reason)
    adapter = load_scope_validation_adapter(world.validator, reason)
    runtime_cases = (
        (world.runtime_alpha_group_a, world.runtime_alpha_group_a, True),
        (world.runtime_alpha_group_a, world.runtime_beta_group_a, False),
        (world.runtime_alpha_group_a, world.runtime_alpha_group_b, False),
        (world.runtime_alpha_group_a, world.runtime_alpha_private, False),
    )
    for required, actual, expected in runtime_cases:
        previous_count = len(adapter.calls)
        allowed = _allowed(
            lambda: adapter.validate_runtime_pair(required=required, actual=actual), reason
        )
        _assert_delegated(adapter, world.validator, previous_count, reason)
        contract_assert(
            allowed is expected,
            reason,
            "RuntimeScope compatibility public-field matrix mismatch",
        )

    raw_derivation = {
        "kind": "EvidenceDerivation",
        "reviewed": False,
        "derivation_version": "v1",
        "policy_version": "scope-derivation/v1",
        "source": asdict(world.catalog),
        "target": asdict(world.runtime_alpha_bot_private),
        "derivation_chain": ("raw",),
    }
    for target in (
        world.runtime_alpha_group_a,
        world.runtime_alpha_private,
        world.runtime_alpha_bot_private,
    ):
        previous_count = len(adapter.calls)
        allowed = _allowed(
            lambda target=target: adapter.validate_evidence_derivation(
                source_catalog=world.catalog,
                target_runtime=target,
                evidence_derivation=raw_derivation,
            ),
            reason,
        )
        _assert_delegated(adapter, world.validator, previous_count, reason)
        contract_assert(
            not allowed,
            reason,
            "raw CatalogScope was accepted as a RuntimeScope target",
        )

    reviewed_derivation = {
        "kind": "EvidenceDerivation",
        "reviewed": True,
        "review_status": "reviewed",
        "derivation_version": "v1",
        "policy_version": "scope-derivation/v1",
        "source": asdict(world.catalog),
        "target": asdict(world.runtime_alpha_bot_private),
        "derivation_chain": (
            "raw",
            "reviewed_projection",
            "scoped_candidate",
            "domain_object",
        ),
    }
    previous_count = len(adapter.calls)
    allowed = _allowed(
        lambda: adapter.validate_evidence_derivation(
            source_catalog=world.catalog,
            target_runtime=world.runtime_alpha_bot_private,
            evidence_derivation=reviewed_derivation,
        ),
        reason,
    )
    _assert_delegated(adapter, world.validator, previous_count, reason)
    contract_assert(
        allowed,
        reason,
        "explicit reviewed versioned EvidenceDerivation target was rejected",
    )

    for missing_stage in ("reviewed_projection", "scoped_candidate"):
        malformed = dict(reviewed_derivation)
        malformed["derivation_chain"] = tuple(
            stage for stage in reviewed_derivation["derivation_chain"] if stage != missing_stage
        )
        previous_count = len(adapter.calls)
        allowed = _allowed(
            lambda malformed=malformed: adapter.validate_evidence_derivation(
                source_catalog=world.catalog,
                target_runtime=world.runtime_alpha_bot_private,
                evidence_derivation=malformed,
            ),
            reason,
        )
        _assert_delegated(adapter, world.validator, previous_count, reason)
        contract_assert(
            not allowed,
            reason,
            f"reviewed derivation missing {missing_stage} was accepted",
        )
