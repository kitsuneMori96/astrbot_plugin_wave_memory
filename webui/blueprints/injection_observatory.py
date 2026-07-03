"""Inject Observatory API — 注入 trace 列表与详情。"""

from __future__ import annotations

import time
from typing import Any, Mapping

try:
    from quart import Blueprint, jsonify, request
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时的轻量兜底
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

    class _Request:
        args: dict[str, Any] = {}
    request = _Request()  # type: ignore[assignment]

try:
    from services.injection.feedback_store import MemoryFeedbackStore
    from services.injection.trace_store import InjectionTraceStore
    from tools.injection_explain import build_injection_explanation
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ...services.injection.feedback_store import MemoryFeedbackStore
    from ...services.injection.trace_store import InjectionTraceStore
    from ...tools.injection_explain import build_injection_explanation

from ..container import get_container
try:
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时直接放行
    def require_auth(func):
        return func

injection_observatory_bp = Blueprint("injection_observatory", __name__, url_prefix="/api/injection")


def _float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def build_trace_list_payload(trace_store: InjectionTraceStore, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """构造 trace 列表响应。"""
    filters = filters or {}
    now = time.time()
    from_ts = _float(filters.get("from_ts") or filters.get("from"), 0.0)
    to_ts = _float(filters.get("to_ts") or filters.get("to"), now)
    limit = max(1, min(_int(filters.get("limit"), 100), 500))
    traces = trace_store.query(
        from_ts=from_ts,
        to_ts=to_ts,
        group_id=filters.get("group_id") or None,
        sender_id=filters.get("sender_id") or None,
        bot_id=filters.get("bot_id") or None,
        channel=filters.get("channel") or None,
        status=filters.get("status") or None,
        has_error=_bool_or_none(filters.get("has_error")),
        scope=filters.get("scope") or filters.get("chat_type") or None,
        limit=limit,
    )
    return {"traces": traces, "count": len(traces), "limit": limit}


def build_trace_detail_payload(
    trace_store: InjectionTraceStore,
    feedback_store: MemoryFeedbackStore | None,
    trace_id: str,
) -> dict[str, Any] | None:
    """构造 trace 详情响应，包含通道命中/过滤和反馈记录。"""
    trace = trace_store.get(trace_id)
    if not trace:
        return None
    payload = build_injection_explanation(trace)
    payload["feedback"] = feedback_store.list_for_trace(trace_id) if feedback_store else []
    return payload


def _stores_from_container() -> tuple[InjectionTraceStore | None, MemoryFeedbackStore | None]:
    c = get_container()
    db = getattr(c, "db", None)
    if not db:
        return None, None
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return None, None
    conn = getattr(db, "conn", None)
    if not conn:
        return None, None
    trace_store = InjectionTraceStore(conn)
    trace_store.ensure_schema()
    feedback_store = MemoryFeedbackStore(conn)
    feedback_store.ensure_schema()
    return trace_store, feedback_store


@injection_observatory_bp.route("/traces", methods=["GET"])
@require_auth
async def list_traces():
    trace_store, _ = _stores_from_container()
    if not trace_store:
        return jsonify({"traces": [], "count": 0, "error": "trace_store_unavailable"})
    return jsonify(build_trace_list_payload(trace_store, dict(getattr(request, "args", {}) or {})))


@injection_observatory_bp.route("/traces/<trace_id>", methods=["GET"])
@require_auth
async def get_trace_detail(trace_id: str):
    trace_store, feedback_store = _stores_from_container()
    if not trace_store:
        return jsonify({"error": "trace_store_unavailable"}), 503
    payload = build_trace_detail_payload(trace_store, feedback_store, trace_id)
    if payload is None:
        return jsonify({"error": "trace_not_found", "trace_id": trace_id}), 404
    return jsonify(payload)


__all__ = ["injection_observatory_bp", "build_trace_list_payload", "build_trace_detail_payload"]
