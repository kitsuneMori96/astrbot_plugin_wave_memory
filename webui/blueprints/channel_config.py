"""Channel Config API — 注入通道热配置观察、校验与应用。"""

from __future__ import annotations

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
        async def get_json(self, *args, **kwargs):
            return {}
    request = _Request()  # type: ignore[assignment]

try:
    from ..container import get_container
    from ..middleware.auth import require_auth
except Exception:  # pragma: no cover
    def get_container():  # type: ignore[no-redef]
        return None

    def require_auth(func):  # type: ignore[no-redef]
        return func

try:
    from services.config.channel_config import (
        MAX_TIMEOUT_MS,
        apply_channel_overrides,
        build_channel_config_from_plugin_config,
        build_default_channel_config,
        channel_config_diff,
    )
    from services.runtime_mode import resolve_runtime_mode
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ...services.config.channel_config import (
        MAX_TIMEOUT_MS,
        apply_channel_overrides,
        build_channel_config_from_plugin_config,
        build_default_channel_config,
        channel_config_diff,
    )
    from ...services.runtime_mode import resolve_runtime_mode


channel_config_bp = Blueprint("channel_config", __name__, url_prefix="/api")

_EDITABLE_FIELDS = ["enabled", "priority", "top_k", "max_items", "token_budget", "timeout_ms", "min_score"]


def _plugin_config(container_or_config: Any) -> Mapping[str, Any]:
    if isinstance(container_or_config, Mapping):
        return container_or_config
    return getattr(container_or_config, "plugin_config", {}) or {}


def _trace_store_from_container(container: Any) -> Any:
    direct = getattr(container, "injection_trace_store", None)
    if direct:
        return direct
    db = getattr(container, "db", None)
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
        try:
            from services.injection.trace_store import InjectionTraceStore
        except Exception:  # pragma: no cover - AstrBot 包导入路径
            from ...services.injection.trace_store import InjectionTraceStore
        store = InjectionTraceStore(conn)
        store.ensure_schema()
        return store
    except Exception:
        return None


def _default_config(plugin_config: Mapping[str, Any]):
    mode = resolve_runtime_mode(plugin_config).mode
    return build_default_channel_config(
        runtime_mode=mode,
        query_cfg=plugin_config.get("Query_Settings", {}) or {},
        inject_cfg=plugin_config.get("Inject_Settings", {}) or {},
    )


def _latest_channel_runtime_stats(trace_store: Any = None) -> dict[str, dict[str, Any]]:
    conn = getattr(trace_store, "conn", None)
    if not conn:
        return {}
    try:
        rows = conn.execute(
            """SELECT c.channel, c.status, c.latency_ms, c.item_count
               FROM injection_trace_channels c
               JOIN injection_traces t ON t.trace_id = c.trace_id
               ORDER BY t.timestamp DESC, c.id DESC"""
        ).fetchall()
    except Exception:
        return {}
    stats: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row[0] or "")
        if not name or name in stats:
            continue
        try:
            latency_ms = int(round(float(row[2] or 0)))
        except (TypeError, ValueError):
            latency_ms = 0
        try:
            hit_count = int(row[3] or 0)
        except (TypeError, ValueError):
            hit_count = 0
        stats[name] = {
            "status": str(row[1] or "unknown"),
            "last_latency_ms": max(0, latency_ms),
            "last_hit_count": max(0, hit_count),
        }
    return stats


def _attach_runtime_status_defaults(config_payload: dict[str, Any], trace_store: Any = None) -> dict[str, Any]:
    stats = _latest_channel_runtime_stats(trace_store)
    for name, channel in (config_payload.get("channels") or {}).items():
        if not isinstance(channel, dict):
            continue
        channel.setdefault("status", "unknown")
        channel.setdefault("last_latency_ms", 0)
        channel.setdefault("last_hit_count", 0)
        channel.update(stats.get(name, {}))
    return config_payload


def build_channel_config_payload(plugin_config: Mapping[str, Any] | None, current_config: Any = None, *, trace_store: Any = None) -> dict[str, Any]:
    """构造 WebUI 通道配置页面所需 payload。"""
    plugin_config = plugin_config or {}
    defaults = _default_config(plugin_config)
    current = current_config or build_channel_config_from_plugin_config(plugin_config)
    runtime = resolve_runtime_mode(plugin_config).to_web_payload()
    current_payload = _attach_runtime_status_defaults(current.to_dict(), trace_store)
    return {
        "runtime": runtime,
        "current": current_payload,
        "defaults": defaults.to_dict(),
        "overrides": plugin_config.get("Channel_Settings", {}) or {},
        "diff": channel_config_diff(defaults, current),
        "editable_fields": list(_EDITABLE_FIELDS),
        "limits": {"timeout_ms_max": MAX_TIMEOUT_MS, "min_score_min": 0, "min_score_max": 1},
    }


def validate_channel_config_patch(
    plugin_config: Mapping[str, Any] | None,
    patch: Mapping[str, Any] | None,
    current_config: Any = None,
) -> dict[str, Any]:
    """校验通道配置补丁并返回候选配置；不修改运行时。"""
    plugin_config = plugin_config or {}
    patch = patch or {}
    current = current_config or build_channel_config_from_plugin_config(plugin_config)
    defaults = _default_config(plugin_config)
    try:
        candidate = apply_channel_overrides(defaults, patch)
    except ValueError as exc:
        return {
            "ok": False,
            "errors": [str(exc)],
            "current": current.to_dict(),
            "candidate": None,
            "diff": [],
        }
    return {
        "ok": True,
        "errors": [],
        "current": current.to_dict(),
        "candidate": candidate.to_dict(),
        "diff": channel_config_diff(current, candidate),
    }


def apply_channel_config_patch(container: Any, patch: Mapping[str, Any] | None) -> dict[str, Any]:
    """校验并应用通道配置到 WebUI 容器和插件运行时 setter。"""
    plugin_config = dict(_plugin_config(container))
    current = getattr(container, "injection_channel_config", None) or build_channel_config_from_plugin_config(plugin_config)
    preview = validate_channel_config_patch(plugin_config, patch, current)
    if not preview.get("ok"):
        return preview

    candidate = apply_channel_overrides(_default_config(plugin_config), patch or {})
    if container is not None:
        setattr(container, "injection_channel_config", candidate)
        setter = getattr(container, "injection_channel_config_setter", None)
        if callable(setter):
            setter(candidate)
        cfg = getattr(container, "plugin_config", None)
        if isinstance(cfg, dict):
            cfg["Channel_Settings"] = dict(patch or {})
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
    return {
        "ok": True,
        "errors": [],
        "current": current.to_dict(),
        "candidate": candidate.to_dict(),
        "diff": channel_config_diff(current, candidate),
        "message": "通道配置已热应用",
    }


def reset_channel_config_to_defaults(container: Any) -> dict[str, Any]:
    """恢复当前运行模式的默认通道配置。"""
    plugin_config = dict(_plugin_config(container))
    current = getattr(container, "injection_channel_config", None) or build_channel_config_from_plugin_config(plugin_config)
    defaults = _default_config(plugin_config)
    if container is not None:
        setattr(container, "injection_channel_config", defaults)
        setter = getattr(container, "injection_channel_config_setter", None)
        if callable(setter):
            setter(defaults)
        cfg = getattr(container, "plugin_config", None)
        if isinstance(cfg, dict):
            cfg["Channel_Settings"] = {}
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
    return {
        "ok": True,
        "errors": [],
        "current": current.to_dict(),
        "candidate": defaults.to_dict(),
        "diff": channel_config_diff(current, defaults),
        "message": "已恢复当前模式默认通道配置",
    }


@channel_config_bp.route("/config/channels", methods=["GET"])
@require_auth
async def get_channel_config():
    c = get_container()
    return jsonify(build_channel_config_payload(
        _plugin_config(c),
        getattr(c, "injection_channel_config", None),
        trace_store=_trace_store_from_container(c),
    ))


@channel_config_bp.route("/config/channels/validate", methods=["POST"])
@require_auth
async def validate_channel_config():
    c = get_container()
    body = await request.get_json(silent=True) or {}
    return jsonify(validate_channel_config_patch(_plugin_config(c), body, getattr(c, "injection_channel_config", None)))


@channel_config_bp.route("/config/channels", methods=["POST"])
@require_auth
async def update_channel_config():
    c = get_container()
    body = await request.get_json(silent=True) or {}
    return jsonify(apply_channel_config_patch(c, body))


@channel_config_bp.route("/config/channels/defaults", methods=["POST"])
@require_auth
async def reset_channel_config():
    c = get_container()
    return jsonify(reset_channel_config_to_defaults(c))


__all__ = [
    "channel_config_bp",
    "build_channel_config_payload",
    "validate_channel_config_patch",
    "apply_channel_config_patch",
    "reset_channel_config_to_defaults",
]
