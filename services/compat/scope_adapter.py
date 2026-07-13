"""Explicit, allowlisted projection from canonical scopes to legacy call shapes."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

try:  # package import in AstrBot runtime
    from ...domain.scope import (
        CatalogScope,
        RuntimeScope,
        ScopeRef,
        ScopeValidationError,
        UnresolvedScopeRef,
    )
except ImportError:  # top-level import in isolated tests
    from domain.scope import (
        CatalogScope,
        RuntimeScope,
        ScopeRef,
        ScopeValidationError,
        UnresolvedScopeRef,
    )

LegacyTarget: TypeAlias = Literal["group", "session", "bot_private", "system"]
LegacyWarningHook: TypeAlias = Callable[[str, Mapping[str, str]], None]
LegacyMetricHook: TypeAlias = Callable[[str, Mapping[str, str]], None]


class LegacyScopeProjectionError(ScopeValidationError):
    """Stable rejection raised by the allowlisted legacy projection boundary."""


_LEGACY_TARGETS = frozenset({"group", "session", "bot_private", "system"})


@dataclass(frozen=True)
class LegacyRuntimeProjection:
    bot_id: str
    visibility: str
    canonical_session_id: str | None
    conversation_id: str | None
    group_id: str | None
    subject_principal_id: str | None
    user_id: str | None


class LegacyScopeAdapter:
    """Project only explicitly allowlisted, information-preserving legacy views."""

    policy_version = "scope-compat-v1"

    def __init__(
        self,
        *,
        allowed_callers: Mapping[str, Collection[LegacyTarget]],
        warning_hook: LegacyWarningHook | None = None,
        metric_hook: LegacyMetricHook | None = None,
    ) -> None:
        if not isinstance(allowed_callers, Mapping):
            raise TypeError("allowed_callers must be a mapping")
        self._allowed_callers = {
            caller: frozenset(targets)
            for caller, targets in allowed_callers.items()
            if isinstance(caller, str) and caller and caller == caller.strip()
        }
        self._warning_hook = warning_hook
        self._metric_hook = metric_hook

    def project_runtime(
        self,
        scope: ScopeRef,
        *,
        caller: str,
        target: LegacyTarget,
        require_subject: bool = False,
    ) -> LegacyRuntimeProjection:
        visibility = scope.visibility if isinstance(scope, RuntimeScope) else "unknown"
        if (
            not isinstance(caller, str)
            or not caller
            or caller != caller.strip()
            or target not in _LEGACY_TARGETS
            or target not in self._allowed_callers.get(caller, frozenset())
        ):
            self._reject(
                caller=caller,
                target=target,
                visibility=visibility,
                reason_code="legacy_caller_not_allowed",
            )

        if isinstance(scope, UnresolvedScopeRef):
            self._reject(
                caller=caller,
                target=target,
                visibility="unknown",
                reason_code="unresolved_scope",
            )
        if isinstance(scope, CatalogScope):
            self._reject(
                caller=caller,
                target=target,
                visibility="unknown",
                reason_code="catalog_runtime_derivation_required",
            )
        if not isinstance(scope, RuntimeScope):
            self._reject(
                caller=caller,
                target=target,
                visibility="unknown",
                reason_code="legacy_projection_visibility_unsupported",
            )

        self._validate_projection_shape(scope, caller=caller, target=target)
        user_id = self._project_user_id(
            scope,
            caller=caller,
            target=target,
            require_subject=require_subject,
        )
        session = scope.session
        projection = LegacyRuntimeProjection(
            bot_id=scope.bot_id,
            visibility=scope.visibility,
            canonical_session_id=session.id if session is not None else None,
            conversation_id=session.conversation_id if session is not None else None,
            group_id=(
                session.conversation_id
                if session is not None and scope.visibility == "group"
                else None
            ),
            subject_principal_id=scope.subject_principal_id,
            user_id=user_id,
        )
        self._emit(
            caller=caller,
            target=target,
            outcome="projected",
            reason_code="legacy_scope_projection",
            visibility=scope.visibility,
        )
        return projection

    def _validate_projection_shape(
        self,
        scope: RuntimeScope,
        *,
        caller: str,
        target: LegacyTarget,
    ) -> None:
        session = scope.session
        if scope.visibility in {"group", "private"}:
            if session is None:
                self._reject(caller, target, scope.visibility, "session_required")
            if session.kind != scope.visibility:
                self._reject(caller, target, scope.visibility, "session_kind_mismatch")
        elif session is not None:
            self._reject(caller, target, scope.visibility, "session_forbidden")

        supported = (
            (target == "group" and scope.visibility == "group")
            or (target == "session" and scope.visibility in {"group", "private"})
            or (target == "bot_private" and scope.visibility == "bot_private")
            or (target == "system" and scope.visibility == "system")
        )
        if not supported:
            self._reject(
                caller,
                target,
                scope.visibility,
                "legacy_projection_visibility_unsupported",
            )

    def _project_user_id(
        self,
        scope: RuntimeScope,
        *,
        caller: str,
        target: LegacyTarget,
        require_subject: bool,
    ) -> str | None:
        principal = scope.subject_principal_id
        if principal is None:
            if require_subject:
                self._reject(
                    caller,
                    target,
                    scope.visibility,
                    "legacy_projection_subject_required",
                )
            return None
        if scope.session is None:
            self._reject(
                caller,
                target,
                scope.visibility,
                "legacy_projection_principal_mismatch",
            )
        prefix = f"{scope.session.platform_id}:user:"
        if not principal.startswith(prefix) or principal == prefix:
            self._reject(
                caller,
                target,
                scope.visibility,
                "legacy_projection_principal_mismatch",
            )
        return principal[len(prefix) :]

    def _reject(
        self,
        caller: Any,
        target: Any,
        visibility: str,
        reason_code: str,
    ) -> None:
        self._emit(
            caller=caller,
            target=target,
            outcome="rejected",
            reason_code=reason_code,
            visibility=visibility,
        )
        raise LegacyScopeProjectionError(reason_code, "legacy scope projection rejected")

    def _emit(
        self,
        *,
        caller: Any,
        target: Any,
        outcome: str,
        reason_code: str,
        visibility: str,
    ) -> None:
        payload = {
            "caller": caller if isinstance(caller, str) else "unknown",
            "target": target if isinstance(target, str) else "unknown",
            "outcome": outcome,
            "reason_code": reason_code,
            "visibility": visibility if isinstance(visibility, str) else "unknown",
            "policy_version": self.policy_version,
        }
        for hook, event_name in (
            (self._warning_hook, "legacy_scope_adapter"),
            (self._metric_hook, "legacy_scope_adapter_total"),
        ):
            if hook is None:
                continue
            try:
                hook(event_name, payload)
            except Exception:
                # Observability must never bypass or break the projection policy.
                pass


__all__ = [
    "LegacyMetricHook",
    "LegacyRuntimeProjection",
    "LegacyScopeAdapter",
    "LegacyScopeProjectionError",
    "LegacyTarget",
    "LegacyWarningHook",
]
