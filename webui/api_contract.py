"""WebUI API 的统一 wire contract 与服务端对象引用。"""

from __future__ import annotations

import hashlib
import hmac
import math
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - AstrBot 包导入路径
    from ..domain.scope import RuntimeScope


def page_response(
    items: list[Any],
    *,
    total: int | None,
    limit: int,
    offset: int,
    unavailable_reason: str = "source_unknown",
) -> dict[str, Any]:
    """构造唯一的嵌套分页响应；未知总数不伪装成零。"""
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        page_count = math.ceil(total / normalized_limit) if total else 0
        page = {
            "total": total,
            "total_status": "exact",
            "reason_code": None,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "page": normalized_offset // normalized_limit + 1,
            "page_count": page_count,
            "has_more": normalized_offset + len(items) < total,
        }
    else:
        page = {
            "total": None,
            "total_status": "unavailable",
            "reason_code": str(unavailable_reason or "source_unknown"),
            "limit": normalized_limit,
            "offset": normalized_offset,
            "page": normalized_offset // normalized_limit + 1,
            "page_count": None,
            "has_more": len(items) >= normalized_limit,
        }
    return {"items": list(items), "page": page}


def error_payload(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> dict[str, Any]:
    """稳定且不携带 locator/scope/version 的错误 envelope。"""
    return {
        "error": {
            "code": str(code),
            "message": str(message),
            "retryable": bool(retryable),
        }
    }


def not_found_payload() -> dict[str, Any]:
    return error_payload("not_found", "Resource not found")


def mutation_response(
    *,
    operation_kind: str,
    status: str,
    revision: int | str | None,
    operation_id: str | None = None,
    item: Any = None,
    include_item: bool = False,
    preflight_token: str | None = None,
) -> dict[str, Any]:
    """构造唯一 mutation envelope；UI 只能按服务端真实 operation/revision 判断结果。"""
    operation = {"kind": str(operation_kind), "status": str(status)}
    if operation_id:
        operation["id"] = str(operation_id)
    payload: dict[str, Any] = {
        "ok": status in {"committed", "succeeded"},
        "operation": operation,
        "revision": revision,
    }
    if include_item:
        payload["item"] = item
    if preflight_token:
        payload["preflight_token"] = str(preflight_token)
    return payload


def field_value_state(
    *,
    default: Any,
    saved: Any,
    effective: Any,
    apply_mode: str,
    effective_since: float | str | None = None,
) -> dict[str, Any]:
    """统一配置 default/saved/effective/apply-mode wire shape。"""
    normalized_mode = str(apply_mode or "unknown")
    if normalized_mode not in {"hot", "restart", "next_run", "unknown"}:
        normalized_mode = "unknown"
    return {
        "default": default,
        "saved": saved,
        "effective": effective,
        "apply_mode": normalized_mode,
        "effective_since": effective_since,
    }


def contract_value(value: Any) -> Any:
    """序列化 ScopeRef、EvidenceRef/Binding、QualityDecision 等领域值对象。"""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("contract value must provide to_dict() or be a mapping")


def current_runtime_scope(provider: Any) -> RuntimeScope | None:
    getter = getattr(provider, "get_request_scope", None)
    if not callable(getter):
        return None
    try:
        value = getter()
    except Exception:
        return None
    return value if isinstance(value, RuntimeScope) else None


@dataclass(frozen=True)
class ObjectBinding:
    """只保存在服务端内存中的对象定位与授权绑定。"""

    kind: str
    locator: int | str
    scope: RuntimeScope
    revision: int


class ObjectRefRegistry:
    """签发不可解码的随机 ObjectRef，并在解析时重新校验 scope/revision。"""

    def __init__(self, *, max_entries: int = 10_000, signing_key: bytes | None = None) -> None:
        self.max_entries = max(100, int(max_entries))
        self._signing_key = signing_key or secrets.token_bytes(32)
        self._bindings: dict[str, ObjectBinding] = {}
        self._refs_by_binding: dict[ObjectBinding, str] = {}

    def _signature(self, nonce: str) -> str:
        return hmac.new(
            self._signing_key,
            nonce.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def _has_valid_signature(self, ref: str) -> bool:
        try:
            prefix, nonce, signature = ref.split(".", 2)
        except ValueError:
            return False
        return (
            prefix == "oref"
            and bool(nonce)
            and hmac.compare_digest(signature, self._signature(nonce))
        )

    def issue(
        self,
        *,
        kind: str,
        locator: int | str,
        scope: RuntimeScope,
        revision: int,
    ) -> str:
        if not isinstance(scope, RuntimeScope):
            raise TypeError("resolved RuntimeScope is required")
        binding = ObjectBinding(str(kind), locator, scope, int(revision))
        existing = self._refs_by_binding.get(binding)
        if existing is not None:
            return existing
        if len(self._bindings) >= self.max_entries:
            stale_ref = next(iter(self._bindings))
            stale = self._bindings.pop(stale_ref)
            self._refs_by_binding.pop(stale, None)
        nonce = secrets.token_urlsafe(24)
        ref = f"oref.{nonce}.{self._signature(nonce)}"
        self._bindings[ref] = binding
        self._refs_by_binding[binding] = ref
        return ref

    def resolve_with_state(
        self,
        ref: Any,
        *,
        kind: str,
        request_scope: RuntimeScope | None,
        locator: int | str | None = None,
    ) -> tuple[ObjectBinding | None, str]:
        """解析 ObjectRef，并为只读深链返回不泄露对象内容的失败状态。"""
        if not isinstance(ref, str) or not self._has_valid_signature(ref):
            return None, "not-found"
        binding = self._bindings.get(ref)
        if (
            binding is None
            or binding.kind != str(kind)
            or (locator is not None and binding.locator != locator)
        ):
            return None, "not-found"
        if not isinstance(request_scope, RuntimeScope) or binding.scope != request_scope:
            return None, "scope-mismatch"
        return binding, "ready"

    def resolve(
        self,
        ref: Any,
        *,
        kind: str,
        locator: int | str,
        request_scope: RuntimeScope | None,
    ) -> ObjectBinding | None:
        binding, state = self.resolve_with_state(
            ref,
            kind=kind,
            locator=locator,
            request_scope=request_scope,
        )
        return binding if state == "ready" else None


def scope_matches_memory_row(scope: RuntimeScope, row: Mapping[str, Any]) -> bool:
    """验证持久化 memory scope 与请求 RuntimeScope 完全兼容。"""
    session = scope.session
    return bool(
        session is not None
        and row.get("bot_id") == scope.bot_id
        and row.get("session_id") == session.id
        and row.get("visibility") == scope.visibility
        and row.get("group_id") == session.conversation_id
        and row.get("resolution_state") == "resolved"
    )


__all__ = [
    "ObjectBinding",
    "ObjectRefRegistry",
    "contract_value",
    "current_runtime_scope",
    "error_payload",
    "field_value_state",
    "mutation_response",
    "not_found_payload",
    "page_response",
    "scope_matches_memory_row",
]
