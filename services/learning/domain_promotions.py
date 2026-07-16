"""学习中心到领域服务的目标适配器。

这些适配器只调用领域 service/repository 的公开方法，不直接操作 WebUI 或领域表。
每个适配器保留候选证据中的 Bot/群/用户作用域，并返回可审计的 target_id。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any, Callable

import numpy as np

try:
    from ...engine.db.book_experience_repo import BookExperienceEpisodeRepository
except ImportError:  # 兼容独立测试/外部调用 services.learning
    from engine.db.book_experience_repo import BookExperienceEpisodeRepository

from .book_experience import BookExperienceEvidenceValidator
from .fewshot_contract import validate_fewshot_candidate_contract
from .scope_policy import (
    LearningPromotionScopeError,
    resolve_learning_promotion_scope,
    resolve_reviewed_catalog_projection,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _evidence(candidate: Mapping[str, Any]) -> dict[str, Any]:
    raw = candidate.get("evidence")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _scope(
    candidate: Mapping[str, Any],
    bot_id: str,
    *,
    command_type: str,
) -> tuple[str, str | None]:
    """Validate canonical promotion scope before a temporary legacy projection."""
    try:
        resolved = resolve_learning_promotion_scope(
            candidate,
            bot_id=bot_id,
            command_type=command_type,
        )
    except LearningPromotionScopeError as exc:
        from .promotion import PromotionTerminalError

        raise PromotionTerminalError(str(exc), code=exc.reason_code) from exc
    return resolved.group_id, resolved.user_id


def _runtime_promotion_context(
    candidate: Mapping[str, Any], bot_id: str, *, command_type: str
):
    try:
        return resolve_learning_promotion_scope(
            candidate,
            bot_id=bot_id,
            command_type=command_type,
        )
    except LearningPromotionScopeError as exc:
        from .promotion import PromotionTerminalError

        raise PromotionTerminalError(str(exc), code=exc.reason_code) from exc


def _catalog_projection_context(candidate: Mapping[str, Any], bot_id: str):
    try:
        return resolve_reviewed_catalog_projection(candidate, bot_id=bot_id)
    except LearningPromotionScopeError as exc:
        from .promotion import PromotionTerminalError

        raise PromotionTerminalError(str(exc), code=exc.reason_code) from exc


def _invoke_legacy(function: Callable[..., Any], values: dict[str, Any]) -> Any:
    """Call a legacy domain method only after explicit Scope projection.

    ``scope`` is intentionally forbidden here: callers must first pass the
    canonical RuntimeScope through ``_scope`` / ``LegacyScopeAdapter``.  The
    remaining signature filtering preserves existing public service variants.
    """
    if "scope" in values:
        _terminal("canonical scope must be projected before legacy invocation", "unprojected_scope")
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return function(**values)
    params = signature.parameters
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return function(**values)
    kwargs = {
        name: value
        for name, value in values.items()
        if name in params
        and params[name].kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    positional = [item for item in params.values() if item.kind == inspect.Parameter.POSITIONAL_ONLY]
    if positional:
        args = [values[name] for name in ("candidate", "bot_id", "target_kind")[: len(positional)] if name in values]
        return function(*args, **kwargs)
    return function(**kwargs)


def _method(service: Any, *names: str) -> Callable[..., Any] | None:
    for name in names:
        candidate = getattr(service, name, None)
        if callable(candidate):
            return candidate
    return None


def _target_id(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("target_id", value.get("id"))
    for name in ("target_id", "id", "event_id"):
        item = getattr(value, name, None)
        if item is not None:
            return item
    return value


def _refresh(service: Any) -> Callable[..., Any] | None:
    return _method(
        service,
        "refresh_index_and_cache",
        "refresh_indexes",
        "refresh_index",
        "refresh_cache",
        "refresh_target",
        "refresh",
    )


def _terminal(message: str, code: str):
    from .promotion import PromotionTerminalError
    raise PromotionTerminalError(message, code=code)


def _result(target_id: Any, service: Any, *, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    target_id = _target_id(target_id)
    if target_id is None or not _text(target_id):
        _terminal("domain service returned no target_id", "missing_target_id")
    refresh = _refresh(service)
    return {
        "target_id": str(target_id),
        "refresh_required": refresh is not None,
        "refresh": refresh,
        "metadata": dict(metadata or {}),
    }


class WorldviewInternalizationPromotionService:
    """受控记忆晋升器；统一使用 ``source=learning``，不复用 legacy bzz source。"""

    def __init__(self, memory_service: Any, *, embedding_service: Any = None, memory_index: Any = None):
        self.service = memory_service
        self.embedding_service = embedding_service
        self.memory_index = memory_index

    def _embedding(self, content: str):
        """在线程执行器中同步取得向量，确保晋升后的记忆可被真实检索。"""
        getter = getattr(self.embedding_service, "get_embedding", None)
        if not callable(getter):
            raise RuntimeError("embedding service is unavailable")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            vector = asyncio.run(getter(content))
        else:
            # Promotion API 在 asyncio.to_thread 中调用；直接在事件循环内调用属于错误接入。
            raise RuntimeError("memory promotion must run outside the event loop")
        if vector is None or len(vector) == 0:
            raise RuntimeError("embedding provider returned no vector")
        return np.asarray(vector, dtype=np.float32)

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        method = _method(self.service, "add_memory")
        if method is None:
            raise ValueError("memory domain service has no add_memory")
        group_id, user_id = _scope(
            candidate,
            bot_id,
            command_type="learning.worldview_internalization.promote",
        )
        requested_source = _text(evidence.get("source"))
        # learning source 是跨领域的稳定语义；拒绝把候选重新伪装成历史 bzz/live source。
        source = requested_source if requested_source.startswith("learning") else "learning"
        content = _text(candidate.get("content"))
        vector = self._embedding(content) if self.embedding_service is not None else None
        target_id = _invoke_legacy(method, {
            "group_id": group_id,
            "content": content,
            "vector": vector,
            "sender_id": user_id or "",
            "sender_name": _text(evidence.get("sender_name")),
            "timestamp": evidence.get("timestamp"),
            "importance": float(evidence.get("importance") or 1.0),
            "source": source,
            "bot_id": bot_id,
            "user_id": user_id,
        })
        # Formal HNSW updates are owned by committed outbox/maintenance projections.
        return _result(target_id, self.service, metadata={
            "source": source,
            "candidate_type": str(candidate.get("candidate_type") or "worldview_internalization"),
            "semantic_label": _text(evidence.get("semantic_label")) or "worldview_internalization",
            "bot_id": bot_id,
            "group_id": group_id,
            "user_id": user_id,
            "vector_indexed": False,
            "derived_state": "pending_outbox_or_repair",
        })


class BookExperiencePromotionService:
    """写入独立书中经历表，绝不调用 ExperienceEpisodeService。"""

    def __init__(self, connection_or_repository: Any, *, now: Callable[[], float] | None = None):
        if isinstance(connection_or_repository, BookExperienceEpisodeRepository):
            self.repository = connection_or_repository
        else:
            connection = getattr(connection_or_repository, "conn", connection_or_repository)
            self.repository = BookExperienceEpisodeRepository(connection, now=now)
        self.refresh_events: list[dict[str, Any]] = []

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        result = BookExperienceEvidenceValidator().validate(evidence)
        if not result.valid:
            detail = ", ".join(result.missing + result.errors) or "invalid evidence"
            _terminal(f"book_experience_episode evidence is insufficient: {detail}", "invalid_evidence")
        group_id, user_id = _scope(
            candidate,
            bot_id,
            command_type="learning.book_experience.promote",
        )
        candidate_id = candidate.get("id")
        key = f"candidate:{candidate_id}" if candidate_id is not None else f"fingerprint:{candidate.get('source_fingerprint', '')}"
        episode_id = self.repository.create(
            bot_id=bot_id,
            group_id=group_id,
            user_id=user_id,
            content=_text(candidate.get("content")),
            evidence=result.normalized,
            idempotency_key=key,
            source_candidate_id=int(candidate_id) if candidate_id is not None else None,
        )
        return {
            "target_id": str(episode_id),
            "refresh_required": True,
            "refresh": self.refresh,
            "metadata": {
                "table": "book_experience_episodes",
                "interactive_experience_table": "experience_episodes",
                "bot_id": bot_id,
                "group_id": group_id,
                "user_id": user_id,
            },
        }

    def refresh(self, **kwargs: Any) -> None:
        self.refresh_events.append(dict(kwargs))


class FewShotStylePromotionService:
    """将批准样例交给可注入 formal writer，不直接执行 SQL/commit。"""

    def __init__(self, writer: Any):
        self.service = writer

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        content = _text(candidate.get("content"))
        health_check = getattr(self.service, "_is_healthy_example", None)
        if callable(health_check) and not health_check(content):
            from .promotion import PromotionTerminalError
            raise PromotionTerminalError("few-shot example failed domain safety validation", code="unsafe_example")
        method = _method(self.service, "write_approved", "add_approved_example")
        if method is None:
            raise ValueError("FewShot formal writer is unavailable")
        try:
            contract = validate_fewshot_candidate_contract(candidate, bot_id=bot_id)
        except ValueError as exc:
            from .promotion import PromotionTerminalError

            raise PromotionTerminalError(
                str(exc), code=str(getattr(exc, "reason_code", "invalid_fewshot_contract"))
            ) from exc
        context = _runtime_promotion_context(
            candidate,
            bot_id,
            command_type="learning.few_shot_style.promote",
        )
        candidate_id = candidate.get("id")
        key = (
            f"candidate:{candidate_id}"
            if candidate_id is not None
            else f"fingerprint:{candidate.get('source_fingerprint', '')}"
        )
        target_id = method(
            scope=context.scope,
            candidate=dict(candidate),
            evidence_refs=context.evidence_refs,
            evidence_bindings=context.evidence_bindings,
            source_tags=contract.source_tags,
            query_trace_id=contract.query_trace_id,
            content=content,
            score=float(evidence.get("score") or 0.0),
            traits=tuple(evidence.get("traits") or ()),
            source_candidate_id=int(candidate_id) if candidate_id is not None else None,
            idempotency_key=key,
        )
        return _result(target_id, self.service, metadata={
            "bot_id": bot_id,
            "runtime_scope": context.scope.to_dict(),
            "status": "approved",
        })


class FactPromotionService:
    """复用 KnowledgeRepo.insert_fact 的污染检测和三元组去重。"""

    def __init__(self, knowledge_service: Any):
        self.service = knowledge_service
        self.calls: list[dict[str, Any]] = []

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        subject = _text(evidence.get("subject"))
        predicate = _text(evidence.get("predicate"))
        obj = _text(evidence.get("object", evidence.get("obj")))
        if not subject or not predicate or not obj:
            from .promotion import PromotionTerminalError
            raise PromotionTerminalError("fact candidate evidence lacks subject/predicate/object", code="invalid_fact")
        method = _method(self.service, "insert_fact")
        if method is None:
            raise ValueError("fact domain service has no insert_fact")
        group_id, user_id = _scope(
            candidate,
            bot_id,
            command_type="learning.fact.promote",
        )
        values = {
            "subject": subject,
            "predicate": predicate,
            "obj": obj,
            "group_id": group_id,
            "source_memory_id": evidence.get("source_memory_id"),
            "confidence": float(evidence.get("confidence") or 0.8),
            "fact_type": evidence.get("fact_type"),
            "bot_id": bot_id,
            "user_id": user_id,
        }
        self.calls.append(dict(values))
        return _result(_invoke_legacy(method, values), self.service, metadata={"bot_id": bot_id, "group_id": group_id, "user_id": user_id})


class RelationshipPromotionService:
    """复用 RelationshipEventService 的维度、事件类型和 cap 校验。"""

    def __init__(self, relationship_service: Any):
        self.service = relationship_service
        self.calls: list[dict[str, Any]] = []

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        method = _method(self.service, "record_event")
        if method is None:
            raise ValueError("relationship domain service has no record_event")
        group_id, user_id = _scope(
            candidate,
            bot_id,
            command_type="learning.relationship.promote",
        )
        if user_id is None:
            from .promotion import PromotionTerminalError
            raise PromotionTerminalError("relationship candidate requires user_id", code="missing_user_scope")
        values = {
            "bot_id": bot_id,
            "group_id": group_id,
            "user_id": user_id,
            "event_type": _text(evidence.get("event_type")),
            "dimension": _text(evidence.get("dimension")),
            "delta": float(evidence.get("delta") or 0.0),
            "reason": _text(evidence.get("reason") or candidate.get("reason")),
            "source_memory_id": evidence.get("source_memory_id"),
            "source_episode_id": evidence.get("source_episode_id"),
            "created_at": evidence.get("created_at"),
        }
        self.calls.append(dict(values))
        return _result(_invoke_legacy(method, values), self.service, metadata={"bot_id": bot_id, "group_id": group_id, "user_id": user_id})


class BookLorePromotionService:
    """写入 reviewed Catalog→Runtime projection；raw Catalog 永远只读。"""

    def __init__(self, writer: Any):
        self.service = writer

    def promote(self, *, candidate: Mapping[str, Any], bot_id: str, target_kind: str):
        evidence = _evidence(candidate)
        method = _method(self.service, "write_reviewed_projection")
        if method is None:
            raise ValueError("reviewed BookLore projection writer is unavailable")
        context = _catalog_projection_context(candidate, bot_id)
        candidate_id = candidate.get("id")
        key = (
            f"candidate:{candidate_id}"
            if candidate_id is not None
            else f"fingerprint:{candidate.get('source_fingerprint', '')}"
        )
        target_id = method(
            source_scope=context.source_scope,
            target_scope=context.target_scope,
            candidate=dict(candidate),
            evidence_refs=context.evidence_refs,
            evidence_bindings=context.evidence_bindings,
            derivation=context.derivation,
            community_id=_text(evidence.get("community_id")),
            title=_text(evidence.get("title")),
            summary=_text(evidence.get("summary_snapshot") or evidence.get("summary")),
            content=_text(candidate.get("content")),
            rank=float(evidence.get("rank") or 0.0),
            status="approved",
            source_candidate_id=int(candidate_id) if candidate_id is not None else None,
            idempotency_key=key,
        )
        return _result(target_id, self.service, metadata={
            "source_catalog_scope": context.source_scope.to_dict(),
            "target_runtime_scope": context.target_scope.to_dict(),
            "status": "approved",
            "derivation_version": context.derivation.derivation_version,
        })


def register_learning_domain_targets(registry: Any, services: Mapping[str, Any]) -> dict[str, Any]:
    """按目标 kind 注册领域适配器，供插件组装时统一接入。"""
    adapters: dict[str, Any] = {}
    constructors = {
        "memory": WorldviewInternalizationPromotionService,
        "book_experience_episode": BookExperiencePromotionService,
        "few_shot": FewShotStylePromotionService,
        "fact": FactPromotionService,
        "relationship": RelationshipPromotionService,
        "book_lore": BookLorePromotionService,
    }
    service_aliases = {
        "memory": ("memory", "worldview_internalization"),
        "book_experience_episode": ("book_experience_episode",),
        "few_shot": ("few_shot", "few_shot_style"),
        "fact": ("fact",),
        "relationship": ("relationship",),
        "book_lore": ("book_lore",),
    }
    for kind, constructor in constructors.items():
        service = next((services.get(alias) for alias in service_aliases[kind] if services.get(alias) is not None), None)
        if service is None:
            continue
        adapter = service if callable(getattr(service, "promote", None)) else constructor(service)
        registry.register(kind, adapter)
        adapters[kind] = adapter
    return adapters


register_default_promotion_targets = register_learning_domain_targets


__all__ = [
    "BookExperiencePromotionService",
    "BookLorePromotionService",
    "FactPromotionService",
    "FewShotStylePromotionService",
    "RelationshipPromotionService",
    "WorldviewInternalizationPromotionService",
    "register_default_promotion_targets",
    "register_learning_domain_targets",
]
