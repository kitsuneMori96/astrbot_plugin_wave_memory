"""LLM Debug API — 请求 trace 列表与详情（token 消耗调试）。"""

from __future__ import annotations

import time
from typing import Any, Mapping

try:
    from quart import Blueprint, jsonify, request
except Exception:
    class Blueprint:
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    def jsonify(value=None, **kwargs):
        return value if value is not None else kwargs

    class _Request:
        args: dict[str, Any] = {}
    request = _Request()

try:
    from services.llm_trace_store import LlmTraceStore
except Exception:
    from ...services.llm_trace_store import LlmTraceStore

from ..container import get_container
try:
    from ..middleware.auth import require_auth
except Exception:
    def require_auth(func):
        return func

llm_debug_bp = Blueprint("llm_debug", __name__, url_prefix="/api/llm-debug")


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


def _store_from_container() -> LlmTraceStore | None:
    c = get_container()
    store = getattr(c, "llm_trace_store", None)
    if not store:
        return None
    try:
        store.ensure_schema()
    except Exception:
        pass
    return store


@llm_debug_bp.route("/traces", methods=["GET"])
@require_auth
async def list_traces():
    store = _store_from_container()
    if not store:
        return jsonify({"traces": [], "count": 0, "error": "llm_trace_store_unavailable"})
    args = getattr(request, "args", {}) or {}
    now = time.time()
    from_ts = _float(args.get("from_ts") or args.get("from"), 0.0)
    to_ts = _float(args.get("to_ts") or args.get("to"), now)
    limit = max(1, min(_int(args.get("limit"), 100), 500))
    traces = store.query(
        from_ts=from_ts,
        to_ts=to_ts,
        group_id=args.get("group_id") or None,
        sender_id=args.get("sender_id") or None,
        limit=limit,
    )
    return jsonify({"traces": traces, "count": len(traces), "limit": limit})


@llm_debug_bp.route("/traces/<trace_id>", methods=["GET"])
@require_auth
async def get_trace_detail(trace_id: str):
    store = _store_from_container()
    if not store:
        return jsonify({"error": "llm_trace_store_unavailable"}), 503
    trace = store.get(trace_id)
    if not trace:
        return jsonify({"error": "trace_not_found", "trace_id": trace_id}), 404
    return jsonify(trace)


@llm_debug_bp.route("/traces", methods=["DELETE"])
@require_auth
async def clear_traces():
    store = _store_from_container()
    if not store:
        return jsonify({"error": "llm_trace_store_unavailable"}), 503
    deleted = store.clear()
    return jsonify({"deleted": deleted})


__all__ = ["llm_debug_bp"]
