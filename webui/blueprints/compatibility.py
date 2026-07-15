"""Compatibility Mode API — LivingMemory 兼容状态与重复注入风险。"""

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
    from services.runtime_mode import resolve_runtime_mode
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ...services.runtime_mode import resolve_runtime_mode


compatibility_bp = Blueprint("compatibility", __name__, url_prefix="/api/compat")

_MEMORY_PLUGIN_IDS = {
    "astrbot_plugin_livingmemory": "LivingMemory",
    "astrbot_plugin_self_learning": "SelfLearning",
    "astrbot_plugin_chatplus": "ChatPlus",
}


def _bool_cfg(config: Mapping[str, Any], section: str, key: str, default: bool = False) -> bool:
    value = (config.get(section, {}) or {}).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _duplicate_warnings(detected_plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for plugin in detected_plugins:
        if not plugin.get("active", False):
            continue
        plugin_id = str(plugin.get("id") or "")
        if plugin_id in _MEMORY_PLUGIN_IDS or str(plugin.get("name") or "") in _MEMORY_PLUGIN_IDS.values():
            warnings.append({
                "plugin_id": plugin_id,
                "name": plugin.get("name") or _MEMORY_PLUGIN_IDS.get(plugin_id, plugin_id),
                "message": "检测到可能重复记忆插件，需检查是否与 WaveMemory 同时自动注入。",
            })
    return warnings


def build_compatibility_payload(
    plugin_config: Mapping[str, Any] | None,
    detection_result: list[dict[str, Any]] | None = None,
    *,
    facade_enabled: bool | None = None,
    aliases_registered: bool | None = None,
) -> dict[str, Any]:
    """构造兼容模式页面 payload。"""
    plugin_config = plugin_config or {}
    runtime = resolve_runtime_mode(plugin_config)
    compat_cfg = plugin_config.get("Compatibility_Settings", {}) or {}
    facade_enabled = bool(facade_enabled if facade_enabled is not None else compat_cfg.get("facade_enabled", False))
    aliases_registered = bool(aliases_registered if aliases_registered is not None else compat_cfg.get("livingmemory_alias_tools_enabled", False))
    detected = list(detection_result or [])
    warnings = _duplicate_warnings(detected)
    return {
        "runtime": runtime.to_web_payload(),
        "facade": {
            "enabled": facade_enabled,
            "status": "enabled" if facade_enabled else "not_initialized",
            "interface": ["search_memories(query, k=5, session_id=None, persona_id=None)", "add_memory(content, session_id=None, persona_id=None, importance=0.7, metadata=None)"],
        },
        "tool_aliases": {
            "recall_long_term_memory": {"enabled": aliases_registered, "target": "livingmemory facade search"},
            "memorize_long_term_memory": {"enabled": aliases_registered, "target": "livingmemory facade add"},
        },
        "detected_plugins": detected,
        "duplicate_warnings": warnings,
        "recommended_settings": [
            "如果使用 SelfLearning/ChatPlus 作为上层学习插件，建议 WaveMemory 切到 compat_only 或 memory_only，避免双重注入。",
            "保留 WaveMemory 作为记忆后端时，请关闭其他插件的自动注入路径，只保留显式记忆读写调用。",
            "检测到重复插件时本页面只提示风险，不会自动修改其他插件配置。",
        ],
    }


def _detect_plugins() -> list[dict[str, Any]]:
    try:
        from services.compat.plugin_detection import detect_memory_plugins  # type: ignore
    except Exception:
        try:
            from ...services.compat.plugin_detection import detect_memory_plugins  # type: ignore
        except Exception:
            return []
    try:
        result = detect_memory_plugins()
        return list(result or [])
    except Exception:
        return []


@compatibility_bp.route("/status", methods=["GET"])
@require_auth
async def get_compatibility_status():
    c = get_container()
    plugin_config = getattr(c, "plugin_config", {}) or {}
    facade_enabled = getattr(c, "livingmemory_facade_enabled", None)
    aliases_registered = getattr(c, "livingmemory_alias_tools_registered", None)
    detected_plugins = getattr(c, "detected_memory_plugins", None) or _detect_plugins()
    return jsonify(build_compatibility_payload(
        plugin_config,
        detected_plugins,
        facade_enabled=facade_enabled,
        aliases_registered=aliases_registered,
    ))


__all__ = ["compatibility_bp", "build_compatibility_payload"]
