"""黑话与信念专属审核桥接。

学习中心只负责把候选交给既有领域审核队列，并把领域状态投影回
``learning_promotions.metadata``。本模块不写 jargon/beliefs 领域表，也不
调用领域的批准动作，因此不会绕过各自页面的安全校验。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class DedicatedReviewError(RuntimeError):
    """专属审核桥接错误。"""

    code = "dedicated_review_error"


class DedicatedReviewUnavailable(DedicatedReviewError):
    """专属审核 service 未加载或暂时不可用。"""

    code = "service_unavailable"


@dataclass(frozen=True)
class DedicatedReviewResult:
    candidate_type: str
    target_id: str | None
    status: str
    deep_link: str | None = None
    error: str | None = None
    metadata: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        payload = {
            "dedicated_candidate_id": self.target_id,
            "dedicated_status": self.status,
            "dedicated_url": self.deep_link,
        }
        if self.error:
            payload["dedicated_error"] = self.error
        if self.metadata:
            payload.update(self.metadata)
        return {key: value for key, value in payload.items() if value is not None}


class DedicatedReviewBridge:
    """适配既有黑话/信念审核服务的最小桥接层。

    注入的 service 可以是现有领域 service、候选 store 的适配器，或测试中的
    facade。桥接只寻找 ``create_candidate``/``link_candidate`` 和
    ``get_review_status``/``get_status`` 等公开接口，不直接执行 approve/reject。
    """

    _KINDS = {
        "jargon_candidate": ("jargon_review", "jargon", "jargon_service"),
        "belief_candidate": ("belief_review", "beliefs", "belief_service"),
    }
    _STATUSES = {"pending", "approved", "rejected", "unknown"}

    def __init__(
        self,
        jargon_service: Any = None,
        belief_service: Any = None,
        services: Mapping[str, Any] | None = None,
        jargon_store: Any = None,
        belief_store: Any = None,
        stores: Mapping[str, Any] | None = None,
        now: Callable[[], float] | None = None,
    ):
        configured = dict(services or {})
        configured_stores = dict(stores or {})
        if jargon_service is not None:
            configured.setdefault("jargon_candidate", jargon_service)
            configured.setdefault("jargon", jargon_service)
        if belief_service is not None:
            configured.setdefault("belief_candidate", belief_service)
            configured.setdefault("belief", belief_service)
        if jargon_store is not None:
            configured_stores.setdefault("jargon_candidate", jargon_store)
            configured_stores.setdefault("jargon", jargon_store)
        if belief_store is not None:
            configured_stores.setdefault("belief_candidate", belief_store)
            configured_stores.setdefault("belief", belief_store)
        self.services = configured
        self.stores = configured_stores
        self.now = now

    @classmethod
    def target_kind_for(cls, candidate_type: str) -> str:
        try:
            return cls._KINDS[str(candidate_type)][0]
        except KeyError as exc:
            raise ValueError(f"unsupported dedicated candidate type: {candidate_type}") from exc

    @classmethod
    def _base_path(cls, candidate_type: str) -> str:
        return cls._KINDS[str(candidate_type)][1]

    @staticmethod
    def _call(function: Callable[..., Any], values: dict[str, Any]) -> Any:
        """按公开方法签名传参，兼容简洁的现有 facade。"""
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError):
            return function(**values)
        parameters = signature.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return function(**values)
        kwargs = {
            name: value
            for name, value in values.items()
            if name in parameters
            and parameters[name].kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        positional = [p for p in parameters.values() if p.kind == inspect.Parameter.POSITIONAL_ONLY]
        if positional:
            args = [values[name] for name in ("candidate", "bot_id", "candidate_type", "target_id")[: len(positional)] if name in values]
            return function(*args, **kwargs)
        return function(**kwargs)

    def _service(self, candidate_type: str) -> Any:
        service = self.services.get(candidate_type)
        if service is None:
            service = self.services.get(self._base_path(candidate_type))
        return service

    def _store(self, candidate_type: str) -> Any:
        store = self.stores.get(candidate_type)
        if store is None:
            store = self.stores.get(self._base_path(candidate_type))
        return store

    @staticmethod
    def _value(result: Any, *names: str, default: Any = None) -> Any:
        if isinstance(result, (str, int, float)) and not isinstance(result, bool):
            if any(name in {"status", "review_status"} for name in names) and isinstance(result, str):
                return result
            if any(name in {"target_id", "candidate_id", "id"} for name in names):
                return result
        if isinstance(result, Mapping):
            for name in names:
                if name in result and result[name] is not None:
                    return result[name]
        else:
            for name in names:
                value = getattr(result, name, None)
                if value is not None:
                    return value
        return default

    @classmethod
    def _status(cls, value: Any, *, default: str = "pending") -> str:
        status = str(value or default).strip().lower()
        # 领域服务可能使用 active/confirmed；它们只作为回读状态映射，
        # 学习中心自身仍保持独立的 approved 状态机。
        status = {"active": "approved", "confirmed": "approved", "denied": "rejected"}.get(status, status)
        return status if status in cls._STATUSES else "unknown"

    @classmethod
    def _deep_link(cls, candidate_type: str, target_id: Any, result: Any = None) -> str | None:
        explicit = cls._value(result, "deep_link", "dedicated_url", "url", default=None)
        if explicit:
            return str(explicit)
        if target_id is None or str(target_id).strip() == "":
            return None
        return f"/{cls._base_path(candidate_type)}?id={target_id}"

    @staticmethod
    def _existing_target_id(candidate: Mapping[str, Any]) -> Any:
        metadata = candidate.get("metadata") or {}
        evidence = candidate.get("evidence") or {}
        for payload in (metadata, evidence):
            for key in ("dedicated_candidate_id", "dedicated_target_id", "domain_candidate_id"):
                if payload.get(key) is not None:
                    return payload[key]
        return None

    def delegate(self, candidate: Mapping[str, Any], *, bot_id: str) -> DedicatedReviewResult:
        """关联/创建领域候选，但不执行领域批准。"""
        candidate_type = str(candidate.get("candidate_type") or "")
        if candidate_type not in self._KINDS:
            raise ValueError(f"unsupported dedicated candidate type: {candidate_type}")
        service = self._service(candidate_type)
        store = self._store(candidate_type) if service is None else None
        if service is None and store is None:
            return DedicatedReviewResult(candidate_type, None, "unknown", error="service_unavailable")
        target_id = self._existing_target_id(candidate)
        result: Any = None
        if target_id is None:
            owner = service or store
            method_names = ("link_candidate", "create_candidate", "create")
            for method_name in method_names:
                method = getattr(owner, method_name, None)
                if callable(method):
                    values = {
                        "candidate": dict(candidate),
                        "bot_id": bot_id,
                        "candidate_type": candidate_type,
                        "content": candidate.get("content", ""),
                        "evidence": candidate.get("evidence") or [],
                        "reason": candidate.get("reason") or "学习中心专属审核委派",
                        "metadata": candidate.get("metadata") or {},
                        "actor": "learning-center",
                    }
                    if owner is store and method_name == "create":
                        values["candidate_type"] = self._base_path(candidate_type)[:-1] if self._base_path(candidate_type).endswith("s") else self._base_path(candidate_type)
                        if isinstance(values["evidence"], Mapping):
                            values["evidence"] = list(values["evidence"].values()) or ["learning-candidate"]
                    try:
                        result = self._call(method, values)
                    except Exception as exc:
                        return DedicatedReviewResult(
                            candidate_type,
                            None,
                            "unknown",
                            error=getattr(exc, "code", "service_unavailable"),
                        )
                    break
            else:
                return DedicatedReviewResult(candidate_type, None, "unknown", error="service_unavailable")
            if isinstance(result, (str, int)) and not isinstance(result, bool):
                target_id = result
            else:
                target_id = self._value(result, "target_id", "candidate_id", "id", default=None)
        status = self._status(self._value(result, "status", "review_status", default="pending"))
        return DedicatedReviewResult(
            candidate_type,
            None if target_id is None else str(target_id),
            status,
            self._deep_link(candidate_type, target_id, result),
            metadata=dict(self._value(result, "metadata", default={}) or {}),
        )

    def status(self, candidate: Mapping[str, Any], *, bot_id: str, target_id: Any = None) -> DedicatedReviewResult:
        """读取领域审核状态；服务不可用时明确返回 unknown。"""
        candidate_type = str(candidate.get("candidate_type") or "")
        if candidate_type not in self._KINDS:
            raise ValueError(f"unsupported dedicated candidate type: {candidate_type}")
        service = self._service(candidate_type)
        store = self._store(candidate_type) if service is None else None
        owner = service or store
        target_id = target_id or self._existing_target_id(candidate)
        if owner is None or target_id is None:
            return DedicatedReviewResult(
                candidate_type,
                None if target_id is None else str(target_id),
                "unknown",
                self._deep_link(candidate_type, target_id),
                error="service_unavailable" if owner is None else "target_id_missing",
            )
        try:
            result = None
            for method_name in ("get_review_status", "get_status", "status", "get_candidate", "get"):
                method = getattr(owner, method_name, None)
                if callable(method):
                    result = self._call(
                        method,
                        {"target_id": target_id, "candidate_id": target_id, "bot_id": bot_id, "candidate_type": candidate_type},
                    )
                    break
            if result is None:
                raise DedicatedReviewUnavailable("service has no status method")
            status = self._status(self._value(result, "status", "review_status", default="unknown"), default="unknown")
            resolved_id = self._value(result, "target_id", "candidate_id", "id", default=target_id)
            return DedicatedReviewResult(
                candidate_type,
                str(resolved_id) if resolved_id is not None else None,
                status,
                self._deep_link(candidate_type, resolved_id, result),
                metadata=dict(self._value(result, "metadata", default={}) or {}),
            )
        except Exception as exc:
            return DedicatedReviewResult(
                candidate_type,
                str(target_id),
                "unknown",
                self._deep_link(candidate_type, target_id),
                error=getattr(exc, "code", "service_unavailable"),
            )

    def sync(self, candidate: Mapping[str, Any], *, bot_id: str, target_id: Any = None) -> DedicatedReviewResult:
        return self.status(candidate, bot_id=bot_id, target_id=target_id)

    # Explicit names make the bridge convenient for integration callers.
    create_or_link = delegate
    get_status = status


__all__ = [
    "DedicatedReviewBridge",
    "DedicatedReviewError",
    "DedicatedReviewResult",
    "DedicatedReviewUnavailable",
]
