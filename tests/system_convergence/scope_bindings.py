"""Thin test-only call adapter for a supplied production ScopeValidator.

This module records delegation and contains no scope compatibility or derivation rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ValidatorCall:
    validator_id: int
    method: str


class _ScopeValidatorBinding:
    def __init__(self, validator: Any):
        self.validator = validator
        self.calls: list[ValidatorCall] = []

    def _record(self, method: str) -> None:
        self.calls.append(ValidatorCall(validator_id=id(self.validator), method=method))

    def validate_runtime_pair(self, *, required: Any, actual: Any) -> Any:
        self._record("runtime_compatibility")
        return self.validator.runtime_compatibility(required=required, actual=actual)

    def validate_unresolved_target(self, *, scope: Any, purpose: str) -> Any:
        self._record("require_target")
        return self.validator.require_target(scope, purpose=purpose)

    def validate_evidence_derivation(
        self,
        *,
        source_catalog: Any,
        target_runtime: Any,
        evidence_derivation: Mapping[str, Any],
    ) -> Any:
        self._record("compatibility")
        return self.validator.compatibility(
            catalog=source_catalog,
            runtime=target_runtime,
            evidence_derivation=evidence_derivation,
        )


def bind_scope_validator(validator: Any) -> _ScopeValidatorBinding:
    return _ScopeValidatorBinding(validator)
