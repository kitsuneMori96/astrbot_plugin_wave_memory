"""Canonical Maintenance job status, logs, checkpoint and cancellation API."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

try:
    from quart import Blueprint, jsonify, request
except Exception:  # pragma: no cover
    class Blueprint:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs): pass
        def route(self, *args, **kwargs):
            def deco(func): return func
            return deco
    def jsonify(value=None, **kwargs):  # type: ignore[no-redef]
        return value if value is not None else kwargs
    class _Request:
        args: dict = {}
    request = _Request()  # type: ignore[assignment]

from ..api_contract import error_payload, mutation_response, not_found_payload, page_response
from ..container import get_container
from ..middleware.auth import require_auth

try:
    from ...services.data_governance_jobs import (
        DataGovernancePreviewError,
        enqueue_preview_job,
    )
    from ...services.scope_recovery import ScopeRecoveryError, enqueue_scope_recovery_preview
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from services.data_governance_jobs import (
        DataGovernancePreviewError,
        enqueue_preview_job,
    )
    from services.scope_recovery import ScopeRecoveryError, enqueue_scope_recovery_preview

maintenance_bp = Blueprint("maintenance", __name__, url_prefix="/api/maintenance")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_payload(run: Any) -> dict[str, Any]:
    value = asdict(run) if is_dataclass(run) else dict(run or {})
    status = str(value.get("status") or "unknown")
    value["status"] = status
    value["operation"] = {
        "id": value.get("run_id"),
        "kind": "maintenance.job.run",
        "status": "queued" if status == "pending" else status,
    }
    value["checkpoint_url"] = f"/api/maintenance/jobs/{value.get('run_id')}/checkpoint"
    value["logs_url"] = f"/api/maintenance/jobs/{value.get('run_id')}/logs"
    value["cancel_url"] = f"/api/maintenance/jobs/{value.get('run_id')}/cancel"
    return value


async def _get_run(run_id: str):
    jobs = getattr(get_container(), "durable_jobs", None)
    if jobs is None:
        return None, (error_payload("durable_jobs_unavailable", "Durable jobs are unavailable", retryable=True), 503)
    run = await jobs.get_run(run_id)
    if run is None:
        return None, (not_found_payload(), 404)
    return run, None


@maintenance_bp.route("/jobs/rebuild", methods=["POST"])
@require_auth
async def schedule_rebuild():
    """Validate an explicit preflight and schedule a durable rebuild job."""
    jobs = getattr(get_container(), "durable_jobs", None)
    if jobs is None:
        return jsonify(error_payload("durable_jobs_unavailable", "Durable jobs are unavailable", retryable=True)), 503
    body = await request.get_json() or {}
    kind = str(body.get("kind") or "memory_index").strip()
    if kind not in {"memory_index", "tag_index", "cooccurrence"}:
        return jsonify(error_payload("unsupported_repair_kind", "Unsupported repair kind")), 400
    preflight_token = str(body.get("preflight_token") or "").strip()
    confirmation = str(body.get("confirmation") or "").strip()
    if not preflight_token or confirmation != "rebuild":
        return jsonify({
            **error_payload("maintenance_confirmation_required", "Explicit rebuild confirmation is required"),
            "required_confirmation": "rebuild",
        }), 409
    schedule_slot = str(body.get("schedule_slot") or preflight_token).strip()
    job_request = await jobs.create_request(
        idempotency_key=str(body.get("idempotency_key") or f"maintenance:{kind}:{preflight_token}"),
        kind=f"maintenance.{kind}.rebuild",
        scope={"kind": "system_maintenance"},
        payload={"kind": kind, "preflight_token": preflight_token, "requested_by": "webui"},
    )
    run = await jobs.schedule_run(
        request_id=job_request.request_id,
        schedule_slot=schedule_slot,
        cursor_generation=int(body.get("cursor_generation") or 0),
        cursor={"phase": "queued"},
    )
    response = mutation_response(
        operation_kind=f"maintenance.{kind}.rebuild",
        operation_id=run.run_id,
        status="queued",
        revision=getattr(run, "updated_at", None),
        item=_run_payload(run),
        include_item=True,
    )
    response["request_id"] = job_request.request_id
    response["job_id"] = run.run_id
    return jsonify(response), 202


@maintenance_bp.route("/data-governance/preview", methods=["POST"])
@require_auth
async def schedule_data_governance_preview():
    """Schedule the fixed, read-only governance preview on the durable runner."""

    container = get_container()
    if getattr(container, "data_governance_jobs", None) is None:
        return jsonify(error_payload(
            "data_governance_preview_unregistered",
            "Data-governance preview handler is not registered",
            retryable=False,
        )), 503
    jobs = getattr(container, "durable_jobs", None)
    if jobs is None:
        return jsonify(error_payload(
            "durable_jobs_unavailable",
            "Durable jobs are unavailable",
            retryable=True,
        )), 503
    body = await request.get_json() or {}
    try:
        envelope = await enqueue_preview_job(jobs, body)
    except DataGovernancePreviewError as exc:
        code = str(exc)
        status = 503 if code == "durable_jobs_unavailable" else 400
        return jsonify(error_payload(code, "Invalid data-governance preview request")), status

    payload = envelope.to_dict() if callable(getattr(envelope, "to_dict", None)) else dict(envelope)
    job_id = payload.get("job_id") or payload.get("operation", {}).get("id")
    payload["dry_run"] = True
    payload["checkpoint_url"] = f"/api/maintenance/jobs/{job_id}/checkpoint"
    payload["cancel_url"] = f"/api/maintenance/jobs/{job_id}/cancel"
    return jsonify(payload), 202


@maintenance_bp.route("/scope-recovery/preview", methods=["POST"])
@require_auth
async def schedule_scope_recovery_preview():
    """Schedule a read-only Legacy -> formal Scope projection preview."""
    container = get_container()
    if not getattr(container, "scope_recovery_jobs", None):
        return jsonify(error_payload(
            "scope_recovery_unregistered",
            "Scope recovery preview handler is not registered",
            retryable=False,
        )), 503
    jobs = getattr(container, "durable_jobs", None)
    if jobs is None:
        return jsonify(error_payload("durable_jobs_unavailable", "Durable jobs are unavailable", retryable=True)), 503
    body = await request.get_json() or {}
    try:
        envelope = await enqueue_scope_recovery_preview(jobs, body)
    except ScopeRecoveryError as exc:
        code = str(exc)
        status = 503 if code == "durable_jobs_unavailable" else 400
        return jsonify(error_payload(code, "Invalid scope recovery preview request")), status
    payload = envelope.to_dict() if callable(getattr(envelope, "to_dict", None)) else dict(envelope)
    job_id = payload.get("job_id") or payload.get("operation", {}).get("id")
    payload.update({
        "dry_run": True,
        "source_business_mutated": False,
        "checkpoint_url": f"/api/maintenance/jobs/{job_id}/checkpoint",
        "cancel_url": f"/api/maintenance/jobs/{job_id}/cancel",
    })
    return jsonify(payload), 202


@maintenance_bp.route("/jobs", methods=["GET"])
@require_auth
async def list_jobs():
    jobs = getattr(get_container(), "durable_jobs", None)
    limit = max(1, min(_safe_int(request.args.get("limit"), 25), 100))
    offset = max(0, _safe_int(request.args.get("offset"), 0))
    if jobs is None:
        return jsonify(page_response([], total=None, limit=limit, offset=offset, unavailable_reason="durable_jobs_unavailable"))
    statuses = [part for part in str(request.args.get("status") or "").split(",") if part]
    runs = await jobs.list_runs(statuses=statuses or None, limit=min(1000, offset + limit + 1))
    items = [_run_payload(run) for run in runs[offset:offset + limit]]
    return jsonify(page_response(items, total=None, limit=limit, offset=offset, unavailable_reason="history_window_unbounded"))


@maintenance_bp.route("/jobs/<run_id>", methods=["GET"])
@require_auth
async def get_job(run_id: str):
    run, failure = await _get_run(run_id)
    if failure is not None:
        payload, status = failure
        return jsonify(payload), status
    return jsonify({"item": _run_payload(run), "checked_at": getattr(run, "updated_at", None), "source": "durable_job_store"})


@maintenance_bp.route("/jobs/<run_id>/checkpoint", methods=["GET"])
@require_auth
async def get_checkpoint(run_id: str):
    run, failure = await _get_run(run_id)
    if failure is not None:
        payload, status = failure
        return jsonify(payload), status
    return jsonify({
        "job_id": run_id,
        "status": getattr(run, "status", "unknown"),
        "checkpoint": getattr(run, "cursor", None),
        "progress": getattr(run, "progress", None),
        "updated_at": getattr(run, "updated_at", None),
        "source": "background_job_runs",
    })


@maintenance_bp.route("/jobs/<run_id>/logs", methods=["GET"])
@require_auth
async def get_logs(run_id: str):
    limit = max(1, min(_safe_int(request.args.get("limit"), 25), 100))
    offset = max(0, _safe_int(request.args.get("offset"), 0))
    run, failure = await _get_run(run_id)
    if failure is not None:
        payload, status = failure
        return jsonify(payload), status
    events = [
        {"level": "info", "event": "scheduled", "at": getattr(run, "created_at", None), "data": {"status": "pending"}},
        {"level": "info", "event": "checkpoint", "at": getattr(run, "updated_at", None), "data": {"status": getattr(run, "status", "unknown"), "progress": getattr(run, "progress", None), "cursor": getattr(run, "cursor", None)}},
    ]
    if getattr(run, "result", None) is not None:
        events.append({"level": "info", "event": "result", "at": getattr(run, "updated_at", None), "data": getattr(run, "result")})
    if getattr(run, "error_code", None) or getattr(run, "error_message", None):
        events.append({"level": "error", "event": "failure", "at": getattr(run, "updated_at", None), "data": {"code": getattr(run, "error_code", None), "message": getattr(run, "error_message", None)}})
    return jsonify(page_response(events[offset:offset + limit], total=len(events), limit=limit, offset=offset))


@maintenance_bp.route("/jobs/<run_id>/cancel", methods=["POST"])
@require_auth
async def cancel_job(run_id: str):
    jobs = getattr(get_container(), "durable_jobs", None)
    if jobs is None:
        return jsonify(error_payload("durable_jobs_unavailable", "Durable jobs are unavailable", retryable=True)), 503
    run = await jobs.request_cancel(run_id)
    if run is None:
        return jsonify(not_found_payload()), 404
    status = str(getattr(run, "status", "unknown"))
    response = mutation_response(
        operation_kind="maintenance.job.cancel",
        operation_id=run_id,
        status="cancelled" if status == "cancelled" else "queued",
        revision=getattr(run, "updated_at", None),
        item=_run_payload(run),
        include_item=True,
    )
    return jsonify(response), 200 if status == "cancelled" else 202


@maintenance_bp.route("/outbox", methods=["GET"])
@require_auth
async def outbox_status():
    """获取 Outbox 事务写队列的运行状态与积压统计。"""
    c = get_container()
    db = getattr(c, "db", None)
    conn = getattr(db, "conn", None) if db else None
    if conn is None:
        return jsonify(error_payload("service_unavailable", "Database is unavailable", retryable=True)), 503

    def _table_count(t: str) -> int:
        try:
            return int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except Exception:
            return 0

    outbox_count = _table_count("domain_outbox")
    deliveries_count = _table_count("outbox_deliveries")
    recovery_items = _table_count("scope_recovery_items")
    job_requests = _table_count("job_requests")

    return jsonify({
        "status": "healthy",
        "outbox_items": outbox_count,
        "outbox_deliveries": deliveries_count,
        "scope_recovery_items": recovery_items,
        "job_requests": job_requests,
        "write_gateway_wired": getattr(c, "write_gateway", None) is not None,
    })


__all__ = ["maintenance_bp"]
