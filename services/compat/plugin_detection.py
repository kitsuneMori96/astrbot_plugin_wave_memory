"""Detect memory-provider plugins that may duplicate WaveMemory injection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

KNOWN_MEMORY_PLUGINS: dict[str, dict[str, Any]] = {
    "astrbot_plugin_livingmemory": {
        "name": "LivingMemory",
        "aliases": {"astrbot_plugin_livingmemory", "livingmemory", "living_memory", "living memory"},
    },
    "astrbot_plugin_self_learning": {
        "name": "SelfLearning",
        "aliases": {"astrbot_plugin_self_learning", "selflearning", "self_learning", "self learning"},
    },
    "astrbot_plugin_chatplus": {
        "name": "ChatPlus",
        "aliases": {"astrbot_plugin_chatplus", "chatplus", "chat_plus", "chat plus"},
    },
}

_COLLECTION_ATTRS = (
    "plugins",
    "loaded_plugins",
    "enabled_plugins",
    "star_registry",
    "star_map",
    "stars",
    "plugin_map",
)

_PLUGIN_KEYS = {
    "id",
    "plugin_id",
    "name",
    "display_name",
    "root_dir_name",
    "module_path",
    "module_name",
    "package",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _safe_get(record: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(record, Mapping):
            return record.get(key, default)
        return getattr(record, key, default)
    except Exception:
        return default


def _is_record_mapping(value: Mapping[str, Any]) -> bool:
    return any(key in value for key in _PLUGIN_KEYS)


def _iter_records(source: Any, *, _seen: set[int] | None = None) -> list[Any]:
    if source is None:
        return []
    if _seen is None:
        _seen = set()
    ident = id(source)
    if ident in _seen:
        return []
    _seen.add(ident)

    if isinstance(source, Mapping):
        if _is_record_mapping(source):
            return [source]
        records: list[Any] = []
        for key, value in source.items():
            if isinstance(value, Mapping):
                merged = {"id": key, **value}
            else:
                merged = {"id": key, "name": value}
            records.extend(_iter_records(merged, _seen=_seen))
        return records

    if isinstance(source, Iterable) and not isinstance(source, (str, bytes)):
        records = []
        for item in source:
            records.extend(_iter_records(item, _seen=_seen))
        return records

    records = []
    for attr in ("_star_manager", "star_manager", "plugin_manager"):
        nested = _safe_get(source, attr)
        if nested is not None:
            records.extend(_iter_records(nested, _seen=_seen))
    for attr in _COLLECTION_ATTRS:
        nested = _safe_get(source, attr)
        if nested is not None:
            records.extend(_iter_records(nested, _seen=_seen))
    if records:
        return records
    return [source]


def _match_known_plugin(record: Any) -> tuple[str, dict[str, Any]] | None:
    values = [
        _safe_get(record, "id"),
        _safe_get(record, "plugin_id"),
        _safe_get(record, "name"),
        _safe_get(record, "display_name"),
        _safe_get(record, "root_dir_name"),
        _safe_get(record, "module_name"),
        _safe_get(record, "module_path"),
        _safe_get(record, "package"),
    ]
    normalized_values = {_norm(value) for value in values if value}
    compact_values = {value.replace("_", " ") for value in normalized_values} | {value.replace("_", "") for value in normalized_values}
    all_values = normalized_values | compact_values
    for plugin_id, spec in KNOWN_MEMORY_PLUGINS.items():
        aliases = {_norm(alias) for alias in spec["aliases"]}
        alias_compact = {alias.replace("_", " ") for alias in aliases} | {alias.replace("_", "") for alias in aliases}
        candidates = aliases | alias_compact
        if all_values & candidates:
            return plugin_id, spec
        if any(alias and any(alias in value for value in all_values) for alias in candidates):
            return plugin_id, spec
    return None


def _active(record: Any) -> bool:
    for key in ("activated", "active", "enabled"):
        value = _safe_get(record, key, None)
        if value is not None:
            if isinstance(value, str):
                return value.strip().lower() not in {"0", "false", "no", "off", "disabled", "inactive"}
            return bool(value)
    status = _safe_get(record, "status", None)
    if status is not None:
        return str(status).strip().lower() not in {"0", "false", "no", "off", "disabled", "inactive", "error"}
    return True


def _display_name(record: Any, spec: Mapping[str, Any]) -> str:
    for key in ("display_name", "name", "id", "root_dir_name"):
        value = _safe_get(record, key)
        if value:
            return str(value)
    return str(spec.get("name", "unknown"))


def _source_label(record: Any) -> str:
    module_path = _safe_get(record, "module_path")
    root = _safe_get(record, "root_dir_name")
    if module_path:
        return str(module_path)
    if root:
        return str(root)
    return record.__class__.__name__


def detect_memory_plugins(*sources: Any, context: Any = None) -> list[dict[str, Any]]:
    """Detect known memory-related plugins from explicit sources or AstrBot registry.

    The function is deliberately best-effort: malformed plugin records are ignored
    so detection never breaks WaveMemory startup or WebUI status rendering.
    """
    records: list[Any] = []
    for source in sources:
        records.extend(_iter_records(source))
    if context is not None:
        records.extend(_iter_records(context))
    if not records:
        try:  # pragma: no cover - depends on AstrBot runtime
            from astrbot.core.star.star import star_registry
            records.extend(_iter_records(star_registry))
        except Exception:
            pass

    detected: dict[str, dict[str, Any]] = {}
    for record in records:
        try:
            match = _match_known_plugin(record)
            if not match:
                continue
            plugin_id, spec = match
            payload = {
                "id": plugin_id,
                "name": spec.get("name") or _display_name(record, spec),
                "active": _active(record),
                "source": _source_label(record),
            }
            existing = detected.get(plugin_id)
            if existing:
                existing["active"] = bool(existing.get("active")) or payload["active"]
                if not existing.get("source") and payload.get("source"):
                    existing["source"] = payload["source"]
            else:
                detected[plugin_id] = payload
        except Exception:
            continue
    return [detected[key] for key in KNOWN_MEMORY_PLUGINS if key in detected]


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def plugin_probe_from_result(
    detected_plugins: list[dict[str, Any]] | None,
    *,
    source: str,
    checked_at: str | None = None,
) -> dict[str, Any]:
    """把一次已完成的 registry 探测结果包装成可判定的事实状态。"""
    detected = list(detected_plugins or [])
    status = "detected" if detected else "not_detected"
    evidence = [
        {
            "kind": "plugin_registry_entry",
            "plugin_id": item.get("id"),
            "name": item.get("name"),
            "active": bool(item.get("active", False)),
            "source": item.get("source") or source,
        }
        for item in detected
    ]
    if not evidence:
        evidence.append({
            "kind": "plugin_registry_probe",
            "summary": "探测已完成，当前 registry 中没有匹配的已知记忆插件。",
            "source": source,
        })
    return {
        "status": status,
        "source": source,
        "checked_at": checked_at or _checked_at(),
        "error": None,
        "evidence": evidence,
        "plugins": detected,
    }


def probe_memory_plugins(*sources: Any, context: Any = None) -> dict[str, Any]:
    """执行一次可诊断的插件探测，不把未配置或异常折叠成空数组。"""
    checked_at = _checked_at()
    source_label = "explicit_sources" if sources else "astrbot.star_registry"
    try:
        if sources or context is not None:
            detected = detect_memory_plugins(*sources, context=context)
            if context is not None and not sources:
                source_label = "astrbot_context"
            return plugin_probe_from_result(detected, source=source_label, checked_at=checked_at)

        try:
            from astrbot.core.star.star import star_registry
        except Exception as exc:
            return {
                "status": "not_configured",
                "source": source_label,
                "checked_at": checked_at,
                "error": str(exc),
                "evidence": [{
                    "kind": "probe_configuration",
                    "summary": "AstrBot plugin registry 在当前进程中不可用，未执行插件探测。",
                    "source": source_label,
                }],
                "plugins": [],
            }
        return plugin_probe_from_result(
            detect_memory_plugins(star_registry),
            source=source_label,
            checked_at=checked_at,
        )
    except Exception as exc:
        return {
            "status": "probe_failed",
            "source": source_label,
            "checked_at": checked_at,
            "error": str(exc),
            "evidence": [{
                "kind": "probe_error",
                "summary": "插件探测执行失败，当前无法判断是否存在兼容冲突。",
                "source": source_label,
            }],
            "plugins": [],
        }


def build_duplicate_memory_warnings(detected_plugins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for plugin in detected_plugins or []:
        if not plugin.get("active", False):
            continue
        plugin_id = str(plugin.get("id") or "")
        if plugin_id not in KNOWN_MEMORY_PLUGINS:
            continue
        warnings.append({
            "plugin_id": plugin_id,
            "name": plugin.get("name") or KNOWN_MEMORY_PLUGINS[plugin_id]["name"],
            "message": "检测到可能重复记忆插件，需检查是否与 WaveMemory 同时自动注入。",
        })
    return warnings


__all__ = [
    "KNOWN_MEMORY_PLUGINS",
    "build_duplicate_memory_warnings",
    "detect_memory_plugins",
    "plugin_probe_from_result",
    "probe_memory_plugins",
]
