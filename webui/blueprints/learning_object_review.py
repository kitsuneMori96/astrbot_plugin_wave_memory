"""Learning Object Review API — 学习对象登记表与审查入口。"""

from __future__ import annotations

from typing import Any, Mapping

try:
    from quart import Blueprint, jsonify
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时的轻量兜底
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

try:
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover
    def get_container():  # type: ignore[no-redef]
        return None

    def require_auth(func):  # type: ignore[no-redef]
        return func

try:
    from services.learning_objects.registry import export_learning_object_registry
    from services.review.candidate_store import ReviewCandidateStore
    from services.runtime_mode import resolve_runtime_mode
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ...services.learning_objects.registry import export_learning_object_registry
    from ...services.review.candidate_store import ReviewCandidateStore
    from ...services.runtime_mode import resolve_runtime_mode


learning_object_review_bp = Blueprint("learning_object_review", __name__, url_prefix="/api/learning-objects")

_CANDIDATE_OBJECT_MAP = {
    "memory": "memory",
    "fact": "facts",
    "belief": "belief",
    "style": "few_shot_style",
    "jargon": "jargon",
}


def _risk_rank(risk: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(risk or ""), 0)


def _pending_candidates(candidate_store: Any = None, *, limit: int = 100) -> list[dict[str, Any]]:
    if not candidate_store:
        return []
    try:
        return candidate_store.list_pending(limit=limit)
    except Exception:
        return []


def _duplicate_entries(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检测学习对象中的重复相关审计发现。

    排除纯文档说明性质（以“风险：”结尾的描述头）的静态文字，
    只返回实际运行时检测到的重复问题（如内容哈希碰撞、相似度匹配等）。
    """
    entries: list[dict[str, Any]] = []
    for item in objects:
        findings = [
            finding for finding in item.get("audit_findings", [])
            if "重复" in str(finding)
            and not str(finding).startswith(("重复写入风险", "重复登记风险"))
        ]
        if findings:
            entries.append({
                "key": item.get("key"),
                "risk": item.get("risk"),
                "findings": findings,
            })
    return entries


def build_learning_object_review_payload(
    plugin_config: Mapping[str, Any] | None,
    candidate_store: Any = None,
) -> dict[str, Any]:
    """构造学习对象审查页面 payload。"""
    plugin_config = plugin_config or {}
    runtime = resolve_runtime_mode(plugin_config)
    objects = export_learning_object_registry()
    for item in objects:
        item["mode_enabled"] = runtime.mode in set(item.get("available_modes", []))
        item["mode_disabled_reason"] = "" if item["mode_enabled"] else f"当前模式 {runtime.mode} 不包含在 available_modes"

    object_by_key = {item["key"]: item for item in objects}
    pending = _pending_candidates(candidate_store)
    enriched_pending = []
    risky_candidates = []
    for candidate in pending:
        item = dict(candidate)
        object_key = _CANDIDATE_OBJECT_MAP.get(str(item.get("candidate_type", "")), str(item.get("candidate_type", "")))
        linked_object = object_by_key.get(object_key, {})
        item["object_key"] = object_key
        item["object_risk"] = linked_object.get("risk", "medium")
        item["mode_enabled"] = bool(linked_object.get("mode_enabled", True))
        enriched_pending.append(item)
        if item["object_risk"] == "high" or not item["mode_enabled"]:
            risky_candidates.append(item)

    high_risk_objects = [item for item in objects if item.get("risk") == "high"]
    disabled_objects = [item for item in objects if not item.get("mode_enabled")]
    return {
        "runtime": runtime.to_web_payload(),
        "objects": objects,
        "pending_candidates": enriched_pending,
        "risky_candidates": risky_candidates,
        "duplicate_entries": _duplicate_entries(objects),
        "summary": {
            "objects": len(objects),
            "high_risk_objects": len(high_risk_objects),
            "mode_disabled_objects": len(disabled_objects),
            "pending_candidates": len(enriched_pending),
            "risky_candidates": len(risky_candidates),
        },
    }


def _candidate_store_from_container() -> ReviewCandidateStore | None:
    c = get_container()
    db = getattr(c, "db", None)
    if not db:
        return None
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return None
    conn = getattr(db, "conn", None)
    if not conn:
        return None
    try:
        store = ReviewCandidateStore(conn)
        store.ensure_schema()
        return store
    except Exception:
        return None


@learning_object_review_bp.route("/review", methods=["GET"])
@require_auth
async def get_learning_object_review():
    c = get_container()
    plugin_config = getattr(c, "plugin_config", {}) or {}
    return jsonify(build_learning_object_review_payload(plugin_config, _candidate_store_from_container()))


__all__ = ["learning_object_review_bp", "build_learning_object_review_payload"]
