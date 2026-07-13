"""审核候选到领域目标的晋升编排。

本模块只依赖目标领域 service 的接口，不直接写 memories/facts/FewShot 等领域表。
每个目标有独立 learning_promotions 记录，因而 correction_learning 可以部分成功、
部分重试；刷新失败也会保留 target_id 并只重试刷新。
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

try:
    from ...engine.db.learning_repository import LearningRepositories
    from ...engine.db.learning_types import PromotionStatus, TargetKind, enum_value
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from engine.db.learning_repository import LearningRepositories
    from engine.db.learning_types import PromotionStatus, TargetKind, enum_value

from .dedicated_review import DedicatedReviewBridge
from .scope_policy import LearningPromotionScopeError, resolve_learning_promotion_scope


class PromotionError(RuntimeError):
    """目标晋升错误基类。"""

    def __init__(self, message: str, *, code: str | None = None, target_id: Any = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if target_id is not None:
            self.target_id = target_id


class PromotionRetryableError(PromotionError):
    """目标或索引暂时不可用，可安全重试。"""


class PromotionTerminalError(PromotionError):
    """候选/目标不满足约束，不应自动重试。"""


# 兼容调用方可能使用的命名。
RetryablePromotionError = PromotionRetryableError
TerminalPromotionError = PromotionTerminalError


@dataclass(frozen=True)
class PromotionResult:
    target_id: str
    refresh_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    refresh: Callable[..., Any] | None = None


@dataclass(frozen=True)
class _RegisteredTarget:
    target: Any
    refresh: Callable[..., Any] | None = None


class DomainPromotionTarget:
    """把现有领域 service 方法适配为 promotion target 合约。

    适配器只调用 ``add_memory``/``insert_fact`` 等公开领域接口，不持有或拼接
    目标表 SQL；复杂目标（FewShot、经历、关系）应注入专属 domain service。
    """

    def __init__(self, service: Any, target_kind: str):
        self.service = service
        self.target_kind = target_kind

    def promote(self, *, candidate: dict[str, Any], bot_id: str, target_kind: str):
        evidence = dict(candidate.get("evidence") or {})
        if target_kind == TargetKind.MEMORY.value:
            method = getattr(self.service, "add_memory", None)
            if not callable(method):
                raise PromotionTerminalError("memory domain service has no add_memory")
            try:
                scope = resolve_learning_promotion_scope(
                    candidate,
                    bot_id=bot_id,
                    command_type="learning.memory.promote",
                )
            except LearningPromotionScopeError as exc:
                raise PromotionTerminalError(str(exc), code=exc.reason_code) from exc
            target_id = method(
                group_id=scope.group_id,
                content=str(candidate.get("content") or ""),
                sender_id=scope.user_id or "",
                sender_name=str(evidence.get("sender_name") or ""),
                timestamp=evidence.get("timestamp"),
                importance=float(evidence.get("importance", 1.0)),
                source=str(evidence.get("source") or "learning"),
            )
            return {"target_id": target_id, "refresh_required": False}
        if target_kind == TargetKind.FACT.value:
            method = getattr(self.service, "insert_fact", None)
            if not callable(method):
                raise PromotionTerminalError("fact domain service has no insert_fact")
            subject = evidence.get("subject")
            predicate = evidence.get("predicate")
            obj = evidence.get("object", evidence.get("obj"))
            if not all(str(value or "").strip() for value in (subject, predicate, obj)):
                raise PromotionTerminalError("fact candidate evidence lacks subject/predicate/object")
            try:
                scope = resolve_learning_promotion_scope(
                    candidate,
                    bot_id=bot_id,
                    command_type="learning.fact.promote",
                )
            except LearningPromotionScopeError as exc:
                raise PromotionTerminalError(str(exc), code=exc.reason_code) from exc
            target_id = method(
                subject=str(subject),
                predicate=str(predicate),
                obj=str(obj),
                group_id=scope.group_id,
                source_memory_id=evidence.get("source_memory_id"),
                confidence=float(evidence.get("confidence", 0.8)),
                fact_type=evidence.get("fact_type"),
            )
            return {"target_id": target_id, "refresh_required": False}
        raise PromotionTerminalError(f"no built-in domain adapter for {target_kind}")


class PromotionTargetRegistry:
    """目标种类到领域晋升 service 的 registry。"""

    def __init__(self):
        self._targets: dict[str, _RegisteredTarget] = {}

    def register(
        self,
        target_kind: str | TargetKind,
        target: Any,
        *,
        refresh: Callable[..., Any] | None = None,
    ) -> Any:
        kind = enum_value(target_kind, TargetKind, "target_kind")
        if target is None:
            raise ValueError("promotion target service is required")
        if (
            not callable(getattr(target, "promote", None))
            and not callable(getattr(target, "create", None))
            and not callable(target)
        ):
            # 目标 service 仍由各领域公开入口负责校验/去重；这里仅注入学习上下文。
            from .domain_promotions import (
                BookExperiencePromotionService,
                BookLorePromotionService,
                FactPromotionService,
                FewShotStylePromotionService,
                RelationshipPromotionService,
                WorldviewInternalizationPromotionService,
            )
            adapters = {
                TargetKind.MEMORY.value: WorldviewInternalizationPromotionService,
                TargetKind.BOOK_EXPERIENCE_EPISODE.value: BookExperiencePromotionService,
                TargetKind.FEW_SHOT.value: FewShotStylePromotionService,
                TargetKind.FACT.value: FactPromotionService,
                TargetKind.RELATIONSHIP.value: RelationshipPromotionService,
                TargetKind.BOOK_LORE.value: BookLorePromotionService,
            }
            constructor = adapters.get(kind)
            if constructor is not None:
                target = constructor(target)
            elif kind in {TargetKind.MEMORY.value, TargetKind.FACT.value}:
                target = DomainPromotionTarget(target, kind)
        self._targets[kind] = _RegisteredTarget(target, refresh)
        return target

    def unregister(self, target_kind: str | TargetKind) -> None:
        self._targets.pop(enum_value(target_kind, TargetKind, "target_kind"), None)

    def resolve(self, target_kind: str | TargetKind) -> Any | None:
        entry = self._targets.get(enum_value(target_kind, TargetKind, "target_kind"))
        return entry.target if entry else None

    def resolve_entry(self, target_kind: str | TargetKind) -> _RegisteredTarget | None:
        return self._targets.get(enum_value(target_kind, TargetKind, "target_kind"))

    def __contains__(self, target_kind: str) -> bool:
        return enum_value(target_kind, TargetKind, "target_kind") in self._targets

    def __len__(self) -> int:
        return len(self._targets)

    get = resolve


# 更短/更语义化的别名，便于依赖注入方使用。
PromotionRegistry = PromotionTargetRegistry
TargetPromotionRegistry = PromotionTargetRegistry


class PromotionOrchestrator:
    """以 promotion 行为边界执行目标写入、刷新、重试和恢复。"""

    _TARGETS: Mapping[str, tuple[str, ...]] = {
        "worldview_internalization": (TargetKind.MEMORY.value,),
        "book_experience_episode": (TargetKind.BOOK_EXPERIENCE_EPISODE.value,),
        "few_shot_style": (TargetKind.FEW_SHOT.value,),
        "fact": (TargetKind.FACT.value,),
        "relationship": (TargetKind.RELATIONSHIP.value,),
        "book_lore": (TargetKind.BOOK_LORE.value,),
        "correction_learning": (TargetKind.MEMORY.value, TargetKind.FACT.value),
        # Task 9 owns these records; Task 7 only leaves an explicit waiting state.
        "jargon_candidate": (TargetKind.JARGON_REVIEW.value,),
        "belief_candidate": (TargetKind.BELIEF_REVIEW.value,),
    }
    _DEDICATED = frozenset({"jargon_review", "belief_review"})

    def __init__(
        self,
        repositories: LearningRepositories,
        registry: PromotionTargetRegistry | None = None,
        *,
        now: Callable[[], float] | None = None,
        policy_version: str = "v1",
        running_timeout: float = 300.0,
        target_services: Mapping[str, Any] | None = None,
        domain_services: Mapping[str, Any] | None = None,
        dedicated_review_bridge: DedicatedReviewBridge | None = None,
    ):
        self.repositories = repositories
        # 空 registry 的 __len__ 为 0，不能用 ``registry or`` 丢掉调用方注入的实例。
        self.registry = registry if registry is not None else PromotionTargetRegistry()
        self.now = now or time.time
        self.policy_version = self._policy_version(policy_version)
        self.running_timeout = float(running_timeout)
        self.dedicated_review_bridge = dedicated_review_bridge
        configured_services = {}
        if target_services:
            configured_services.update(target_services)
        if domain_services:
            configured_services.update(domain_services)
        service_aliases = {
            "worldview_internalization": TargetKind.MEMORY.value,
            "book_experience_episode": TargetKind.BOOK_EXPERIENCE_EPISODE.value,
            "few_shot_style": TargetKind.FEW_SHOT.value,
            "fact": TargetKind.FACT.value,
            "relationship": TargetKind.RELATIONSHIP.value,
            "book_lore": TargetKind.BOOK_LORE.value,
        }
        for target_kind, service in configured_services.items():
            self.registry.register(service_aliases.get(str(target_kind), target_kind), service)

    @staticmethod
    def _policy_version(value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("policy_version is required")
        return value

    @staticmethod
    def make_idempotency_key(candidate_id: int, target_kind: str, policy_version: str) -> str:
        return f"candidate:{int(candidate_id)}:target:{target_kind}:policy:{policy_version}"

    @classmethod
    def target_kinds_for(cls, candidate_type: str) -> tuple[str, ...]:
        try:
            return cls._TARGETS[str(candidate_type)]
        except KeyError as exc:
            raise ValueError(f"candidate type has no promotion policy: {candidate_type}") from exc

    def enqueue(
        self,
        candidate_id: int,
        *,
        bot_id: str,
        target_kind: str | TargetKind | None = None,
        policy_version: str | None = None,
        requested_by: str | None = None,
    ) -> list[dict[str, Any]]:
        candidate = self.repositories.candidates.get(candidate_id, bot_id=bot_id)
        if not candidate:
            raise ValueError("candidate not found for bot_id")
        if candidate["review_status"] != "approved":
            if candidate["review_status"] == "delegated":
                return self.repositories.promotions.list_for_candidate(candidate_id, bot_id=bot_id)
            raise ValueError("candidate must be approved before promotion")
        version = self._policy_version(policy_version or self.policy_version)
        kinds = (enum_value(target_kind, TargetKind, "target_kind"),) if target_kind else self.target_kinds_for(candidate["candidate_type"])
        records = []
        for kind in kinds:
            records.append(
                self.repositories.promotions.create(
                    bot_id=bot_id,
                    candidate_id=candidate_id,
                    target_kind=kind,
                    idempotency_key=self.make_idempotency_key(candidate_id, kind, version),
                    requested_by=requested_by,
                    metadata={
                        "policy_version": version,
                        "candidate_type": candidate["candidate_type"],
                    },
                )
            )
        return [self.repositories.promotions.get(item, bot_id=bot_id) for item in records]

    @staticmethod
    def _call(function: Callable[..., Any], values: dict[str, Any]) -> Any:
        """按目标 service 签名注入上下文，兼容 keyword-only 和简洁 fake service。"""
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):
            return function(**values)
        parameters = signature.parameters
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
            return function(**values)
        kwargs = {
            name: value
            for name, value in values.items()
            if name in parameters
            and parameters[name].kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind == inspect.Parameter.POSITIONAL_ONLY
        ]
        if positional:
            args = [values[name] for name in ("candidate", "bot_id", "target_kind", "target_id")[: len(positional)] if name in values]
            return function(*args, **kwargs)
        return function(**kwargs)

    def _promote_target(
        self,
        target: _RegisteredTarget,
        *,
        candidate: dict[str, Any],
        bot_id: str,
        target_kind: str,
    ) -> tuple[str, bool, dict[str, Any], Callable[..., Any] | None]:
        service = target.target
        function = getattr(service, "promote", None)
        if not callable(function):
            function = getattr(service, "create", None)
        if not callable(function) and callable(service):
            function = service
        if not callable(function):
            raise PromotionTerminalError(f"target service for {target_kind} has no promote method")
        result = self._call(
            function,
            {"candidate": candidate, "bot_id": bot_id, "target_kind": target_kind},
        )
        refresh: Callable[..., Any] | None = target.refresh
        if isinstance(result, PromotionResult):
            target_id = result.target_id
            required = bool(result.refresh_required)
            metadata = dict(result.metadata)
            refresh = result.refresh or refresh
        elif isinstance(result, Mapping):
            target_id = result.get("target_id", result.get("id"))
            required = bool(result.get("refresh_required", result.get("refresh", True)))
            metadata = dict(result.get("metadata") or {})
            candidate_refresh = result.get("refresh")
            if callable(candidate_refresh):
                refresh = candidate_refresh
        elif hasattr(result, "target_id"):
            target_id = getattr(result, "target_id")
            required = bool(getattr(result, "refresh_required", True))
            metadata = dict(getattr(result, "metadata", {}) or {})
            refresh = getattr(result, "refresh", None) or refresh
        else:
            target_id = result
            required = True
            metadata = {}
        if target_id is None or str(target_id).strip() == "":
            raise PromotionTerminalError(f"target service for {target_kind} returned no target_id")
        if refresh is None:
            for name in (
                "refresh", "refresh_index", "refresh_cache", "refresh_indexes",
                "refresh_index_and_cache", "refresh_target",
            ):
                method = getattr(service, name, None)
                if callable(method):
                    refresh = method
                    break
        return str(target_id), required, metadata, refresh

    def _refresh(
        self,
        function: Callable[..., Any],
        *,
        target_id: str,
        candidate: dict[str, Any],
        bot_id: str,
        target_kind: str,
    ) -> Any:
        return self._call(
            function,
            {
                "target_id": target_id,
                "candidate": candidate,
                "bot_id": bot_id,
                "target_kind": target_kind,
            },
        )

    def _sync_dedicated(
        self,
        promotion: dict[str, Any],
        *,
        candidate: dict[str, Any],
        bot_id: str,
    ) -> dict[str, Any]:
        """把专属审核回读投影到晋升视图，不修改领域对象。"""
        bridge = self.dedicated_review_bridge
        if bridge is None:
            return promotion
        result = bridge.sync(
            candidate,
            bot_id=bot_id,
            target_id=promotion.get("target_id")
            or (promotion.get("metadata") or {}).get("dedicated_candidate_id"),
        )
        metadata = dict(promotion.get("metadata") or {})
        metadata.update(result.as_metadata())
        metadata["dedicated_sync_at"] = float(self.now())
        if result.status == "approved":
            status = PromotionStatus.SUCCEEDED
            error_code = None
            error_message = None
        elif result.status == "rejected":
            status = PromotionStatus.TERMINAL_FAILED
            error_code = "dedicated_review_rejected"
            error_message = "dedicated review rejected"
        else:
            status = PromotionStatus.WAITING_DEDICATED_REVIEW
            error_code = result.error or "dedicated_review_unknown"
            error_message = "dedicated review state is unavailable"
        return self.repositories.promotions.update_status(
            promotion["id"],
            bot_id=bot_id,
            promotion_status=status,
            target_id=result.target_id or promotion.get("target_id"),
            error_code=error_code,
            error_message=error_message,
            finished_at=float(self.now()),
            metadata=metadata,
        )

    @staticmethod
    def _classify_error(exc: BaseException) -> tuple[str, str]:
        if isinstance(exc, PromotionTerminalError) or getattr(exc, "terminal", False):
            return "terminal_failed", getattr(exc, "code", "terminal_error")
        return "retryable_failed", getattr(exc, "code", "promotion_error")

    def execute(self, promotion_id: int, *, bot_id: str) -> dict[str, Any] | None:
        """执行一条 promotion；已成功记录只读返回，重试不会重复创建目标。"""
        current = self.repositories.promotions.get(promotion_id, bot_id=bot_id)
        if not current:
            return None
        if current["promotion_status"] in {
            PromotionStatus.SUCCEEDED.value,
            PromotionStatus.TERMINAL_FAILED.value,
            PromotionStatus.DELEGATED.value,
        }:
            return current
        if current["promotion_status"] == PromotionStatus.WAITING_DEDICATED_REVIEW.value:
            if self.dedicated_review_bridge is None:
                return current
            candidate = self.repositories.candidates.get(current["candidate_id"], bot_id=bot_id)
            return self._sync_dedicated(current, candidate=candidate, bot_id=bot_id) if candidate else current
        if current["promotion_status"] == PromotionStatus.RUNNING.value:
            started = current.get("started_at")
            if started is None or float(self.now()) - float(started) < self.running_timeout:
                return current
            self.recover_interrupted(bot_id=bot_id, timeout=self.running_timeout)
        claimed = self.repositories.promotions.claim(
            promotion_id, bot_id=bot_id, started_at=float(self.now())
        )
        if not claimed:
            return self.repositories.promotions.get(promotion_id, bot_id=bot_id)
        candidate = self.repositories.candidates.get(claimed["candidate_id"], bot_id=bot_id)
        if not candidate:
            return self.repositories.promotions.update_status(
                promotion_id,
                bot_id=bot_id,
                promotion_status=PromotionStatus.TERMINAL_FAILED,
                error_code="candidate_missing",
                error_message="candidate no longer exists",
                finished_at=float(self.now()),
            )
        kind = claimed["target_kind"]
        if kind in self._DEDICATED:
            delegated = self.repositories.promotions.update_status(
                promotion_id,
                bot_id=bot_id,
                promotion_status=PromotionStatus.WAITING_DEDICATED_REVIEW,
                target_id=claimed.get("target_id"),
                finished_at=float(self.now()),
                metadata={**claimed["metadata"], "delegated": True},
            )
            return self._sync_dedicated(delegated, candidate=candidate, bot_id=bot_id)
        registered = self.registry.resolve_entry(kind)
        if not registered:
            return self.repositories.promotions.update_status(
                promotion_id,
                bot_id=bot_id,
                promotion_status=PromotionStatus.RETRYABLE_FAILED,
                error_code="target_service_unavailable",
                error_message=f"no promotion target registered for {kind}",
                finished_at=float(self.now()),
            )

        target_id = claimed.get("target_id")
        metadata = dict(claimed.get("metadata") or {})
        refresh_required = True
        refresh = registered.refresh
        try:
            if not target_id:
                target_id, refresh_required, target_metadata, refresh = self._promote_target(
                    registered, candidate=candidate, bot_id=bot_id, target_kind=kind
                )
                metadata.update(target_metadata)
                metadata["target_written"] = True
                metadata["refresh_pending"] = bool(refresh_required)
                # 先持久化目标 ID，再刷新；刷新失败时重试绝不再次创建目标。
                self.repositories.promotions.update_status(
                    promotion_id,
                    bot_id=bot_id,
                    promotion_status=PromotionStatus.RUNNING,
                    target_id=target_id,
                    metadata=metadata,
                )
            else:
                refresh_required = bool(metadata.get("refresh_pending", True))
                if refresh is None:
                    refresh = registered.refresh
                    if refresh is None:
                        service = registered.target
                        for name in (
                            "refresh", "refresh_index", "refresh_cache", "refresh_indexes",
                            "refresh_index_and_cache", "refresh_target",
                        ):
                            method = getattr(service, name, None)
                            if callable(method):
                                refresh = method
                                break
            if refresh_required and refresh is not None:
                self._refresh(
                    refresh,
                    target_id=str(target_id),
                    candidate=candidate,
                    bot_id=bot_id,
                    target_kind=kind,
                )
            metadata["refresh_pending"] = False
            metadata["target_written"] = True
            return self.repositories.promotions.update_status(
                promotion_id,
                bot_id=bot_id,
                promotion_status=PromotionStatus.SUCCEEDED,
                target_id=str(target_id),
                finished_at=float(self.now()),
                metadata=metadata,
            )
        except Exception as exc:
            status, code = self._classify_error(exc)
            if not target_id:
                target_id = getattr(exc, "target_id", None)
            metadata["target_written"] = bool(target_id)
            metadata["refresh_pending"] = bool(target_id)
            return self.repositories.promotions.update_status(
                promotion_id,
                bot_id=bot_id,
                promotion_status=status,
                target_id=str(target_id) if target_id else None,
                error_code=code,
                error_message=str(exc)[:500],
                finished_at=float(self.now()),
                metadata=metadata,
            )

    def promote_candidate(self, candidate_id: int, *, bot_id: str) -> list[dict[str, Any]]:
        candidate = self.repositories.candidates.get(candidate_id, bot_id=bot_id)
        if not candidate:
            raise ValueError("candidate not found for bot_id")
        existing = self.repositories.promotions.list_for_candidate(candidate_id, bot_id=bot_id)
        if not existing and candidate["review_status"] == "approved":
            existing = self.enqueue(candidate_id, bot_id=bot_id)
        return [self.execute(item["id"], bot_id=bot_id) for item in existing]

    def retry(self, promotion_id: int, *, bot_id: str) -> dict[str, Any] | None:
        item = self.repositories.promotions.get(promotion_id, bot_id=bot_id)
        if not item:
            return None
        if item["promotion_status"] != PromotionStatus.RETRYABLE_FAILED.value:
            return item
        return self.execute(promotion_id, bot_id=bot_id)

    def recover_interrupted(self, *, bot_id: str, timeout: float | None = None) -> int:
        return self.repositories.promotions.recover_stale(
            bot_id=bot_id,
            now=float(self.now()),
            timeout=self.running_timeout if timeout is None else timeout,
        )

    # 便捷别名：保留 service 层调用方的自然命名，同时共享同一实现。
    enqueue_candidate = enqueue
    run_promotion = execute
    promote = execute
    retry_promotion = retry
    recover = recover_interrupted


__all__ = [
    "DomainPromotionTarget",
    "PromotionError",
    "PromotionOrchestrator",
    "PromotionRegistry",
    "PromotionResult",
    "PromotionRetryableError",
    "PromotionTargetRegistry",
    "PromotionTerminalError",
    "RetryablePromotionError",
    "TargetPromotionRegistry",
    "TerminalPromotionError",
]
