"""学习中心后端 API。

该蓝图只调用学习中心仓储/服务，不直接写入 memory、facts、FewShot 或专属审核
领域表。所有资源都以显式 BotProfile.db_id 为作用域，返回统一错误结构。
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Mapping

try:
    from quart import Blueprint, current_app, jsonify, request
except Exception:  # pragma: no cover - Quart 未安装时允许 helper 导入
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            self.name = args[0] if args else "learning_center"

        def route(self, *args, **kwargs):
            def decorator(function):
                return function
            return decorator

    current_app = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]

    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs

try:
    from ..api_contract import mutation_response, page_response
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover - package 相对导入兼容
    def get_container():  # type: ignore[no-redef]
        return None

    def require_auth(function):  # type: ignore[no-redef]
        return function

try:
    from domain.scope import RuntimeScope
    from engine.db.learning_repository import LearningRepositories
    from engine.db.learning_types import CandidateType, PromotionStatus, ReviewStatus
    from services.learning.dedicated_review import DedicatedReviewBridge
    from services.learning.job_runner import LearningJobRunner
    from services.learning.promotion import PromotionOrchestrator
    from services.learning.review import LearningReviewService
    from services.learning.source import LearningSourceRegistry
    from services.learning.legacy import read_legacy_projections
except Exception:  # pragma: no cover - AstrBot 插件包导入路径
    from ...domain.scope import RuntimeScope
    from ...engine.db.learning_repository import LearningRepositories
    from ...engine.db.learning_types import CandidateType, PromotionStatus, ReviewStatus
    from ...services.learning.dedicated_review import DedicatedReviewBridge
    from ...services.learning.job_runner import LearningJobRunner
    from ...services.learning.promotion import PromotionOrchestrator
    from ...services.learning.review import LearningReviewService
    from ...services.learning.source import LearningSourceRegistry
    from ...services.learning.legacy import read_legacy_projections


learning_center_bp = Blueprint("learning_center", __name__, url_prefix="/api/learning-center")


# ---------------------------------------------------------------------------
# 通用边界：错误、Bot、分页、容器服务
# ---------------------------------------------------------------------------


def api_error(code: str, message: str, status: int = 400, *, retryable: bool = False, details: Any = None):
    """返回稳定的错误 envelope；message 不暴露跨 Bot 对象是否存在。"""
    error: dict[str, Any] = {"code": str(code), "message": str(message), "retryable": bool(retryable)}
    if details is not None:
        error["details"] = details
    # 新客户端读取 error，极简旧客户端也可直接读取顶层别名。
    return jsonify({
        "error": error,
        "code": error["code"],
        "message": error["message"],
        "retryable": error["retryable"],
    }), int(status)


def _connection(container: Any):
    db = getattr(container, "db", None)
    if db is None:
        return None
    if getattr(db, "closed", False):
        try:
            db.reopen()
        except Exception:
            return None
    return getattr(db, "conn", None)


def _repositories(container: Any) -> LearningRepositories | None:
    existing = getattr(container, "learning_repositories", None)
    if existing is not None:
        return existing
    db = getattr(container, "db", None)
    existing = getattr(db, "learning", None) if db is not None else None
    if existing is not None:
        container.learning_repositories = existing
        return existing
    conn = _connection(container)
    if conn is None:
        return None
    try:
        existing = LearningRepositories.from_connection(conn)
        container.learning_repositories = existing
        return existing
    except Exception:
        return None


def _services(container: Any, repositories: LearningRepositories):
    """解析容器注入的服务；测试/旧启动路径缺失时使用薄兼容实例。"""
    bridge = getattr(container, "learning_dedicated_review_bridge", None)
    if bridge is None:
        bridge = DedicatedReviewBridge(
            jargon_service=getattr(container, "jargon_service", None),
            belief_service=getattr(container, "belief_service", None),
        )
        container.learning_dedicated_review_bridge = bridge
    review = getattr(container, "learning_review_service", None)
    if review is None:
        review = LearningReviewService(repositories, dedicated_review_bridge=bridge)
        container.learning_review_service = review
    promotion = getattr(container, "learning_promotion_orchestrator", None)
    if promotion is None:
        promotion = PromotionOrchestrator(repositories, dedicated_review_bridge=bridge)
        container.learning_promotion_orchestrator = promotion
    return review, promotion, bridge


def _runner(container: Any, repositories: LearningRepositories):
    runner = getattr(container, "learning_job_runner", None)
    if runner is not None:
        return runner
    registry = getattr(container, "learning_source_registry", None)
    if registry is None:
        registry = LearningSourceRegistry()
    runner = LearningJobRunner(repositories, registry)
    container.learning_source_registry = registry
    container.learning_job_runner = runner
    return runner


def _request_key(body: Mapping[str, Any] | None = None) -> str | None:
    if request is not None:
        value = request.headers.get("Idempotency-Key")
        if value:
            return str(value).strip() or None
    body = body or {}
    for key in ("idempotency_key", "request_id", "request_key"):
        value = body.get(key)
        if value:
            return str(value).strip() or None
    return None


def _cached(container: Any, operation: str, bot_id: str, key: str | None):
    if not key:
        return None
    cache = getattr(container, "learning_api_idempotency", None)
    if cache is None:
        cache = {}
        container.learning_api_idempotency = cache
    result = cache.get((operation, bot_id, key))
    return copy.deepcopy(result) if result is not None else None


def _cache(container: Any, operation: str, bot_id: str, key: str | None, payload: Any):
    if key:
        cache = getattr(container, "learning_api_idempotency", None)
        if cache is None:
            cache = {}
            container.learning_api_idempotency = cache
        cache[(operation, bot_id, key)] = copy.deepcopy(payload)
    return payload


async def _json_body() -> dict[str, Any]:
    if request is None:
        return {}
    try:
        value = await request.get_json(silent=True)
    except Exception:
        value = None
    return dict(value) if isinstance(value, Mapping) else {}


def _scope(body: Mapping[str, Any] | None = None) -> str:
    value = request.args.get("bot_id") if request is not None else None
    if not value and body:
        value = body.get("bot_id")
    value = str(value or "").strip()
    if not value:
        raise ValueError("bot_id is required")
    if value.isdecimal():
        raise ValueError("bot_id must be BotProfile.db_id, not a QQ number")
    return value


def _page_args() -> tuple[int, int]:
    values = request.args if request is not None else {}
    raw_limit = values.get("limit", values.get("size", 50))
    raw_offset = values.get("offset", 0)
    limit = int(raw_limit)
    offset = int(raw_offset)
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    return limit, offset


def _optional_float(*names: str) -> float | None:
    values = request.args if request is not None else {}
    value = next((values.get(name) for name in names if values.get(name) not in (None, "")), None)
    if value in (None, ""):
        return None
    return float(value)


def _marker_values(value: Any) -> set[str]:
    """提取质量/legacy 标记；只匹配显式状态，不猜测正文语义。"""
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {
                "decision", "quality_decision", "quality_state", "legacy_state", "state",
                "reason_code", "reason_codes", "traceability", "availability",
            }:
                values.update(_marker_values(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            values.update(_marker_values(item))
    elif value is not None:
        values.add(str(value).strip().lower())
    return values


def _candidate_guard(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """把不可自动晋升条件投影为稳定、可测试的只读状态。"""
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), Mapping) else {}
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), Mapping) else {}
    markers = _marker_values(evidence) | _marker_values(metadata)
    legacy = bool(candidate.get("legacy_kind") or metadata.get("legacy") or evidence.get("legacy"))
    legacy_unlinked = bool(legacy and (
        not candidate.get("source_id")
        or not candidate.get("job_id")
        or markers.intersection({"unavailable", "unresolved", "legacy_unlinked"})
    ))
    quarantined = bool(markers.intersection({"quarantine", "quarantined"}))
    content = str(candidate.get("content") or "")
    garbled = "\ufffd" in content or bool(markers.intersection({"text_garbled", "mojibake", "garbled"}))
    reasons: list[str] = []
    if legacy_unlinked:
        reasons.append("legacy_unlinked")
    if quarantined:
        reasons.append("quarantined")
    if garbled:
        reasons.append("text_garbled")
    return {
        "legacy_unlinked": legacy_unlinked,
        "quarantined": quarantined,
        "garbled": garbled,
        "promotion_blocked": bool(reasons),
        "promotion_block_reasons": reasons,
    }


def _candidate_scope(candidate: Mapping[str, Any]) -> RuntimeScope | None:
    for owner_name in ("metadata", "evidence"):
        owner = candidate.get(owner_name)
        if not isinstance(owner, Mapping):
            continue
        for key in ("target_scope", "runtime_scope", "scope"):
            value = owner.get(key)
            if isinstance(value, Mapping) and value.get("kind") == "RuntimeScope":
                value = value.get("payload")
            if isinstance(value, Mapping):
                try:
                    return RuntimeScope.from_dict(value)
                except Exception:
                    continue
    # FewShot 正式资源是 Bot-scoped；这不是默认 Bot，而是候选自身的 bot_id。
    if candidate.get("candidate_type") == CandidateType.FEW_SHOT_STYLE.value:
        try:
            return RuntimeScope(bot_id=str(candidate["bot_id"]), visibility="bot_private", session=None)
        except Exception:
            return None
    return None


def _target_link(candidate: Mapping[str, Any], promotion: Mapping[str, Any]) -> dict[str, Any] | None:
    if promotion.get("promotion_status") != PromotionStatus.SUCCEEDED.value or promotion.get("target_id") in (None, ""):
        return None
    target_kind = str(promotion.get("target_kind") or "")
    paths = {
        "few_shot": "/knowledge/fewshot",
        "jargon_review": "/jargon",
        "belief_review": "/beliefs",
    }
    path = paths.get(target_kind)
    scope = _candidate_scope(candidate)
    if path is None or scope is None or current_app is None:
        return None
    try:
        refs = current_app.extensions.get("wave_api_contract", {}).get("object_refs")
        if refs is None:
            return None
        revision = int(promotion.get("id") or 0)
        ref = refs.issue(
            kind=target_kind,
            locator=promotion["target_id"],
            scope=scope,
            revision=revision,
        )
        session_id = scope.session.id if scope.session is not None else "bot_private"
        return {
            "path": path,
            "object_ref": {
                "ref": ref,
                "kind": target_kind,
                "scope_key": f"{scope.bot_id}:{session_id}",
                "version": revision,
            },
        }
    except Exception:
        return None


def _view_promotion(
    repositories: LearningRepositories,
    promotion: Mapping[str, Any],
    *,
    candidate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = dict(promotion)
    scoped_candidate = candidate or repositories.candidates.get(
        int(item["candidate_id"]), bot_id=str(item["bot_id"])
    )
    if scoped_candidate:
        item["candidate_type"] = scoped_candidate.get("candidate_type")
        item["target_link"] = _target_link(scoped_candidate, item)
    else:
        item["target_link"] = None
    item["retryable"] = item.get("promotion_status") == PromotionStatus.RETRYABLE_FAILED.value
    return item


def _view_candidate(repositories: LearningRepositories, candidate: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    item = dict(candidate)
    item.update(_candidate_guard(item))
    bot_id = item["bot_id"]
    source_id = item.get("source_id")
    job_id = item.get("job_id")
    item["source"] = repositories.sources.get(source_id, bot_id=bot_id) if source_id else None
    item["task"] = repositories.jobs.get(job_id, bot_id=bot_id) if job_id else None
    raw_promotions = repositories.promotions.list_for_candidate(item["id"], bot_id=bot_id)
    promotions = [_view_promotion(repositories, promotion, candidate=item) for promotion in raw_promotions]
    item["promotions"] = promotions
    statuses = {str(p.get("promotion_status") or "") for p in promotions}
    if not statuses:
        item["promotion_status"] = None
    elif len(statuses) == 1:
        item["promotion_status"] = next(iter(statuses))
    elif "succeeded" in statuses:
        item["promotion_status"] = "partial"
    else:
        item["promotion_status"] = "mixed"
    item["target_ids"] = [p["target_id"] for p in promotions if p.get("target_id") is not None]
    item["failures"] = [
        {"id": p["id"], "code": p.get("error_code"), "message": p.get("error_message"), "retryable": p.get("promotion_status") == "retryable_failed"}
        for p in promotions if p.get("error_code") or p.get("error_message")
    ]
    if detail:
        operations: list[dict[str, Any]] = []
        if item.get("reviewer") or item.get("reviewed_at") is not None:
            operations.append({
                "kind": "review",
                "status": item.get("review_status"),
                "actor": item.get("reviewer"),
                "at": item.get("reviewed_at"),
                "note": item.get("review_note"),
            })
        for promotion in promotions:
            operations.append({
                "kind": "promotion",
                "id": promotion["id"],
                "status": promotion.get("promotion_status"),
                "actor": promotion.get("requested_by"),
                "at": promotion.get("finished_at") or promotion.get("started_at") or promotion.get("created_at"),
                "target_id": promotion.get("target_id"),
                "error_code": promotion.get("error_code"),
                "error_message": promotion.get("error_message"),
            })
        item["operations"] = operations
    return item


def _run_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "__dataclass_fields__"):
        return {name: getattr(result, name) for name in result.__dataclass_fields__}
    if isinstance(result, Mapping):
        return dict(result)
    return {"status": str(result)}


def _status_code_for_exception(exc: Exception) -> tuple[str, int, bool]:
    code = str(getattr(exc, "code", "") or "")
    if code in {"idempotency_conflict", "duplicate"}:
        return code, 409, False
    if "not found" in str(exc).lower() or "does not belong" in str(exc).lower():
        return "not_found", 404, False
    if "required" in str(exc).lower() or "invalid" in str(exc).lower():
        return code or "invalid_request", 400, False
    if isinstance(exc, (RuntimeError, OSError)):
        return "service_unavailable", 503, True
    return code or "learning_center_error", 400, bool(getattr(exc, "retryable", False))


def _safe_call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error(code, str(exc)[:300], status, retryable=retryable)


# ---------------------------------------------------------------------------
# 来源与任务
# ---------------------------------------------------------------------------


@learning_center_bp.route("/sources", methods=["GET"])
@require_auth
async def list_sources():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        items, total = repositories.sources.list(bot_id=bot_id, limit=limit, offset=offset)
        return jsonify(page_response(items, total=total, limit=limit, offset=offset))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/sources", methods=["POST"])
@require_auth
async def create_source():
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        previous = _cached(container, "create_source", bot_id, key)
        if previous is not None:
            return jsonify(previous)
        source_id = repositories.sources.create(
            bot_id=bot_id,
            source_type=body.get("source_type", body.get("type", "")),
            name=body.get("name", ""),
            enabled=bool(body.get("enabled", True)),
            config=dict(body.get("config") or {}),
            cursor=dict(body.get("cursor") or {}) if body.get("cursor") is not None else None,
        )
        payload = _cache(container, "create_source", bot_id, key, {"item": repositories.sources.get(source_id, bot_id=bot_id), "ok": True})
        return jsonify(payload), 201
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/sources/<int:source_id>", methods=["PATCH"])
@require_auth
async def update_source(source_id: int):
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        previous = _cached(container, "update_source:" + str(source_id), bot_id, key)
        if previous is not None:
            return jsonify(previous)
        item = repositories.sources.update(
            source_id,
            bot_id=bot_id,
            enabled=body.get("enabled") if "enabled" in body else None,
            config=dict(body["config"]) if "config" in body else None,
            cursor=dict(body["cursor"]) if "cursor" in body else None,
        )
        if item is None:
            return api_error("not_found", "source not found", 404)
        payload = _cache(container, "update_source:" + str(source_id), bot_id, key, {"item": item, "ok": True})
        return jsonify(payload)
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/jobs", methods=["GET"])
@require_auth
async def list_jobs():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        items, total = repositories.jobs.list(bot_id=bot_id, limit=limit, offset=offset)
        return jsonify(page_response(items, total=total, limit=limit, offset=offset))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/jobs", methods=["POST"])
@require_auth
async def create_job():
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        previous = _cached(container, "create_job", bot_id, key)
        if previous is not None:
            return jsonify(previous)
        job_id = repositories.jobs.create(
            bot_id=bot_id,
            source_id=int(body.get("source_id")),
            candidate_type=body.get("candidate_type", ""),
            name=body.get("name", ""),
            enabled=bool(body.get("enabled", True)),
            schedule=dict(body.get("schedule") or {}),
            policy=dict(body.get("policy") or {}),
        )
        payload = _cache(container, "create_job", bot_id, key, {"item": repositories.jobs.get(job_id, bot_id=bot_id), "ok": True})
        return jsonify(payload), 201
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/jobs/<int:job_id>", methods=["PATCH"])
@require_auth
async def update_job(job_id: int):
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        operation = "update_job:" + str(job_id)
        previous = _cached(container, operation, bot_id, key)
        if previous is not None:
            return jsonify(previous)
        item = repositories.jobs.update(
            job_id,
            bot_id=bot_id,
            enabled=bool(body["enabled"]) if "enabled" in body else None,
            name=body.get("name") if "name" in body else None,
            schedule=dict(body["schedule"]) if "schedule" in body else None,
            policy=dict(body["policy"]) if "policy" in body else None,
        )
        if item is None:
            return api_error("not_found", "job not found", 404)
        return jsonify(_cache(container, operation, bot_id, key, {"item": item, "ok": True}))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/jobs/<int:job_id>/run", methods=["POST"])
@require_auth
async def run_job(job_id: int):
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        previous = _cached(container, "run_job:" + str(job_id), bot_id, key)
        if previous is not None:
            return jsonify(previous)
        runner = _runner(container, repositories)
        result = runner.manual_run(job_id, bot_id=bot_id)
        if asyncio.iscoroutine(result):
            result = await result
        item = _run_result(result)
        payload = mutation_response(
            operation_kind="learning.job.run",
            status=str(item.get("status") or "unknown"),
            revision=item.get("run_id") or item.get("id") or job_id,
            item=item,
            include_item=True,
        )
        payload = _cache(container, "run_job:" + str(job_id), bot_id, key, payload)
        return jsonify(payload), 202
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


# ---------------------------------------------------------------------------
# 候选、审核、晋升
# ---------------------------------------------------------------------------


@learning_center_bp.route("/candidates", methods=["GET"])
@require_auth
async def list_candidates():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        items, total = repositories.candidates.list(
            bot_id=bot_id,
            limit=limit,
            offset=offset,
            candidate_type=request.args.get("candidate_type") or request.args.get("type"),
            review_status=request.args.get("review_status"),
            promotion_status=request.args.get("promotion_status"),
            source=request.args.get("source"),
            since=_optional_float("since", "start_time", "created_after", "from"),
            until=_optional_float("until", "end_time", "created_before", "to"),

        )
        return jsonify(page_response(
            [_view_candidate(repositories, item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        ))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/candidates/<int:candidate_id>", methods=["GET"])
@require_auth
async def get_candidate(candidate_id: int):
    try:
        bot_id = _scope()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        candidate = repositories.candidates.get(candidate_id, bot_id=bot_id)
        if candidate is None:
            return api_error("not_found", "candidate not found", 404)
        return jsonify({"item": _view_candidate(repositories, candidate, detail=True)})
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/candidates/<int:candidate_id>/review", methods=["POST"])
@require_auth
async def review_candidate(candidate_id: int):
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        operation = "review_candidate:" + str(candidate_id)
        previous = _cached(container, operation, bot_id, key)
        if previous is not None:
            return jsonify(previous)
        production_wired = getattr(container, "learning_promotion_orchestrator", None) is not None
        review, promotion, _ = _services(container, repositories)
        action = body.get("action", body.get("review_status", body.get("status")))
        action_text = str(getattr(action, "value", action) or "").strip().lower()
        current_candidate = repositories.candidates.get(candidate_id, bot_id=bot_id)
        if current_candidate is None:
            return api_error("not_found", "candidate not found", 404)
        guard = _candidate_guard(current_candidate)
        if action_text in {"approve", "approved", "delegate", "delegated"} and guard["promotion_blocked"]:
            return api_error(
                "candidate_not_promotable",
                "candidate is read-only and cannot be promoted",
                409,
                details={"reason_codes": guard["promotion_block_reasons"]},
            )
        reviewer = body.get("reviewer", body.get("actor", ""))
        result = review.review(
            candidate_id,
            bot_id=bot_id,
            action=action,
            reviewer=reviewer,
            note=body.get("note", body.get("review_note", body.get("reason"))),
        )
        candidate = result.get("candidate") if isinstance(result, Mapping) else None
        promotions = result.get("promotions", []) if isinstance(result, Mapping) else []
        if production_wired and action_text in {"approve", "approved"} and promotions:
            # 领域写入可能包含 embedding/SQLite 操作，放到线程中，避免阻塞 Quart/AstrBot 事件循环。
            promotions = await asyncio.to_thread(
                promotion.promote_candidate,
                candidate_id,
                bot_id=bot_id,
            )
        refreshed_candidate = repositories.candidates.get(candidate_id, bot_id=bot_id)
        item = {
            "candidate": _view_candidate(repositories, refreshed_candidate, detail=True) if refreshed_candidate else None,
            "promotions": [
                _view_promotion(repositories, value, candidate=refreshed_candidate)
                for value in promotions
            ],
        }
        payload = mutation_response(
            operation_kind="learning.candidate.review",
            status="committed",
            revision=(refreshed_candidate or {}).get("updated_at"),
            item=item,
            include_item=True,
        )
        payload = _cache(container, operation, bot_id, key, payload)
        return jsonify(payload)
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/promotions", methods=["GET"])
@require_auth
async def list_promotions():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        items, total = repositories.promotions.list(
            bot_id=bot_id,
            limit=limit,
            offset=offset,
            candidate_type=request.args.get("candidate_type") or request.args.get("type"),
            promotion_status=request.args.get("promotion_status"),
            target_kind=request.args.get("target_kind"),
            source=request.args.get("source"),
            since=_optional_float("since", "start_time", "created_after", "from"),
            until=_optional_float("until", "end_time", "created_before", "to"),
        )
        return jsonify(page_response(
            [_view_promotion(repositories, item) for item in items],
            total=total,
            limit=limit,
            offset=offset,
        ))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/promotions/<int:promotion_id>/retry", methods=["POST"])
@require_auth
async def retry_promotion(promotion_id: int):
    body = await _json_body()
    try:
        container = get_container()
        bot_id = _scope(body)
        repositories = _repositories(container)
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        key = _request_key(body)
        operation = "retry_promotion:" + str(promotion_id)
        previous = _cached(container, operation, bot_id, key)
        if previous is not None:
            return jsonify(previous)
        _, promotion, _ = _services(container, repositories)
        current = repositories.promotions.get(promotion_id, bot_id=bot_id)
        if current is None:
            return api_error("not_found", "promotion not found", 404)
        if current.get("promotion_status") != PromotionStatus.RETRYABLE_FAILED.value:
            return api_error("promotion_not_retryable", "promotion is not retryable", 409, retryable=False)
        candidate = repositories.candidates.get(int(current["candidate_id"]), bot_id=bot_id)
        guard = _candidate_guard(candidate or {})
        if guard["promotion_blocked"]:
            return api_error(
                "candidate_not_promotable",
                "candidate is read-only and cannot be promoted",
                409,
                details={"reason_codes": guard["promotion_block_reasons"]},
            )
        result = await asyncio.to_thread(promotion.retry, promotion_id, bot_id=bot_id)
        item = _view_promotion(repositories, result, candidate=candidate)
        payload = mutation_response(
            operation_kind="learning.promotion.retry",
            status=str(item.get("promotion_status") or "unknown"),
            revision=item.get("updated_at") or item.get("id"),
            item=item,
            include_item=True,
        )
        payload = _cache(container, operation, bot_id, key, payload)
        return jsonify(payload)
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


# ---------------------------------------------------------------------------
# 分类视图与专属审核状态
# ---------------------------------------------------------------------------


@learning_center_bp.route("/few-shot", methods=["GET"])
@require_auth
async def list_few_shot():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        candidates, total = repositories.candidates.list(
            bot_id=bot_id, candidate_type=CandidateType.FEW_SHOT_STYLE.value, limit=limit, offset=offset
        )
        # 这里只返回 style candidate/review/promotion 过程；正式 approved 样例由 FewShot 资源 API 管理。
        return jsonify(page_response(
            [_view_candidate(repositories, item) for item in candidates],
            total=total,
            limit=limit,
            offset=offset,
        ))
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/experiences", methods=["GET"])
@require_auth
async def list_experiences():
    try:
        bot_id = _scope()
        limit, offset = _page_args()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        worldview, worldview_total = repositories.candidates.list(bot_id=bot_id, candidate_type=CandidateType.WORLDVIEW_INTERNALIZATION.value, limit=limit, offset=offset)
        book, book_total = repositories.candidates.list(bot_id=bot_id, candidate_type=CandidateType.BOOK_EXPERIENCE_EPISODE.value, limit=limit, offset=offset)
        # legacy memories 的历史 source 无稳定 Bot 列，迁移约定只投影到 baizz；
        # 其他 Bot 必须得到空历史，不能因旧表缺 bot_id 而泄漏对象。
        projections = read_legacy_projections(repositories.connection, bot_id=bot_id) if bot_id == "baizz" else {
            "evolution_history": [], "legacy_experience_history": [], "interaction_experiences": []
        }
        interaction_all = projections.get("interaction_experiences", [])
        evolution_all = projections.get("evolution_history", [])
        legacy_experience_all = projections.get("legacy_experience_history", [])
        window = slice(offset, offset + limit)
        interaction = interaction_all[window]
        return jsonify({
            "worldview_internalization": [_view_candidate(repositories, item) for item in worldview],
            "book_experience_episodes": [_view_candidate(repositories, item) for item in book],
            "interaction_experiences": interaction,
            "legacy_history": {
                "evolution": evolution_all[window],
                "experience": legacy_experience_all[window],
            },
            "pagination": {"limit": limit, "offset": offset, "has_more": offset + max(len(worldview), len(book), len(interaction), len(evolution_all[window]), len(legacy_experience_all[window])) < max(worldview_total, book_total, len(interaction_all), len(evolution_all), len(legacy_experience_all))},
            "labels": {"worldview_internalization": "世界观内化（非书中真实经历）", "book_experience_episodes": "书中经历", "interaction_experiences": "互动经历", "legacy_history": "legacy 历史经历"},
        })
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


@learning_center_bp.route("/dedicated-review-status/<int:candidate_id>", methods=["GET"])
@require_auth
async def dedicated_review_status(candidate_id: int):
    try:
        bot_id = _scope()
        repositories = _repositories(get_container())
        if repositories is None:
            return api_error("service_unavailable", "learning repositories are unavailable", 503, retryable=True)
        candidate = repositories.candidates.get(candidate_id, bot_id=bot_id)
        if candidate is None:
            return api_error("not_found", "candidate not found", 404)
        if candidate.get("candidate_type") not in {CandidateType.JARGON_CANDIDATE.value, CandidateType.BELIEF_CANDIDATE.value}:
            return api_error("dedicated_review_unsupported", "candidate has no dedicated review", 422)
        _, _, bridge = _services(get_container(), repositories)
        promotions = repositories.promotions.list_for_candidate(candidate_id, bot_id=bot_id)
        dedicated = next((item for item in promotions if item.get("target_kind") in {"jargon_review", "belief_review"}), None)
        target_id = dedicated.get("target_id") if dedicated else None
        if target_id is None and dedicated:
            target_id = (dedicated.get("metadata") or {}).get("dedicated_candidate_id")
        result = bridge.status(candidate, bot_id=bot_id, target_id=target_id)
        target_kind = "jargon_review" if candidate["candidate_type"] == CandidateType.JARGON_CANDIDATE.value else "belief_review"
        link_promotion = {
            "id": (dedicated or {}).get("id") or candidate_id,
            "target_kind": target_kind,
            "target_id": result.target_id,
            "promotion_status": PromotionStatus.SUCCEEDED.value,
        }
        target_link = _target_link(candidate, link_promotion)
        return jsonify({"item": {
            "candidate_id": candidate_id,
            "candidate_type": candidate["candidate_type"],
            "target_id": result.target_id,
            "status": result.status,
            "deep_link": target_link["path"] if target_link else None,
            "object_ref": target_link["object_ref"] if target_link else None,
            "error": result.error,
            "metadata": result.metadata or {},
            "promotion": _view_promotion(repositories, dedicated, candidate=candidate) if dedicated else None,
        }})
    except Exception as exc:
        code, status, retryable = _status_code_for_exception(exc)
        return api_error("bot_id_required" if "bot_id" in str(exc) else code, str(exc)[:300], status, retryable=retryable)


__all__ = ["api_error", "learning_center_bp"]
