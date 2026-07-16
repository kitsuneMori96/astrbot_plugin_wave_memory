"""请求级分层有效配置解析器。

配置按 system -> bot -> session/group -> user -> relationship 顺序覆盖。
缺键和 ``None`` 继承上一层；显式 ``False``、``0``、空字符串和空列表保留。
本模块只做纯函数解析/校验，不读取或修改 AstrBot schema、HotConfig 或运行时状态。
"""

from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from ...domain.scope import RuntimeScope
except ImportError:  # 兼容独立测试/顶级 services 导入
    from domain.scope import RuntimeScope


LAYER_ORDER = ("system", "bot", "session", "user", "relationship")
_SCOPED_LAYERS = frozenset(LAYER_ORDER[1:])
_LAYER_ALIASES = {"group": "session"}
_APPLY_MODE_RANK = {"hot": 0, "next_request": 1, "next_run": 1, "restart": 2}


class EffectiveConfigError(ValueError):
    """分层配置拒绝，携带稳定 reason_code。"""

    def __init__(self, reason_code: str, message: str = "") -> None:
        self.reason_code = reason_code
        self.code = reason_code
        self.message = message or reason_code
        super().__init__(f"{reason_code}: {self.message}")


@dataclass(frozen=True)
class EffectiveConfigResult:
    values: dict[str, Any]
    provenance: dict[str, dict[str, Any]]
    revision: str
    apply_mode: str
    restart_required: bool
    restart_paths: tuple[str, ...]
    applied_layers: tuple[dict[str, Any], ...]
    scope: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": copy.deepcopy(self.values),
            "provenance": copy.deepcopy(self.provenance),
            "revision": self.revision,
            "apply_mode": self.apply_mode,
            "restart_required": self.restart_required,
            "restart_paths": list(self.restart_paths),
            "applied_layers": copy.deepcopy(list(self.applied_layers)),
            "scope": copy.deepcopy(self.scope),
        }


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise EffectiveConfigError("invalid_config_mapping", f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise EffectiveConfigError("invalid_config_key", f"{field} keys must be strings")
    return value


def _json_copy(value: Any, *, field: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise EffectiveConfigError("non_json_config_value", f"{field} must contain JSON values") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _normalize_layer(layer: Any) -> str:
    raw = str(layer or "").strip()
    normalized = _LAYER_ALIASES.get(raw, raw)
    if normalized not in LAYER_ORDER:
        raise EffectiveConfigError("invalid_config_layer", f"unsupported config layer: {raw!r}")
    return normalized


def _require_scope(scope: Any) -> RuntimeScope:
    if not isinstance(scope, RuntimeScope):
        raise EffectiveConfigError("runtime_scope_required", "canonical RuntimeScope is required")
    return scope


def scope_selector(layer: str, scope: RuntimeScope) -> dict[str, str]:
    """从正式 RuntimeScope 派生某层 selector，绝不接受动态 ID 猜测。"""
    normalized = _normalize_layer(layer)
    scope = _require_scope(scope)
    if normalized == "system":
        return {}
    if normalized == "bot":
        return {"bot_id": scope.bot_id}
    if scope.session is None:
        raise EffectiveConfigError("session_scope_required", f"{normalized} layer requires a session")
    selector = {
        "bot_id": scope.bot_id,
        "visibility": scope.visibility,
        "session_id": scope.session.id,
    }
    if normalized in {"user", "relationship"}:
        if scope.subject_principal_id is None:
            raise EffectiveConfigError("scope_subject_required", f"{normalized} layer requires subject_principal_id")
        selector["subject_principal_id"] = scope.subject_principal_id
    return selector


def _expected_selector_fields(layer: str) -> set[str]:
    normalized = _normalize_layer(layer)
    if normalized == "bot":
        return {"bot_id"}
    if normalized == "session":
        return {"bot_id", "visibility", "session_id"}
    if normalized in {"user", "relationship"}:
        return {"bot_id", "visibility", "session_id", "subject_principal_id"}
    return set()


def _validate_selector(layer: str, selector: Any) -> dict[str, str]:
    normalized = _normalize_layer(layer)
    raw = _mapping(selector, field=f"layers.{normalized}.selector")
    expected = _expected_selector_fields(normalized)
    if set(raw) != expected:
        raise EffectiveConfigError(
            "invalid_layer_selector",
            f"{normalized} selector fields must be exactly {sorted(expected)!r}",
        )
    result: dict[str, str] = {}
    for key in sorted(expected):
        value = raw.get(key)
        if not isinstance(value, str) or not value or value != value.strip():
            raise EffectiveConfigError("invalid_layer_selector", f"{normalized}.{key} must be a non-empty exact string")
        result[key] = value
    if normalized == "session" and result["visibility"] not in {"group", "private"}:
        raise EffectiveConfigError("invalid_layer_selector", "session layer visibility must be group or private")
    if normalized in {"user", "relationship"} and result["visibility"] not in {"group", "private"}:
        raise EffectiveConfigError("invalid_layer_selector", f"{normalized} layer visibility must be group or private")
    return result


def _leaf_paths(value: Mapping[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    leaves: list[tuple[str, Any]] = []
    for key in sorted(value):
        path = f"{prefix}.{key}" if prefix else key
        item = value[key]
        if isinstance(item, Mapping) and item:
            leaves.extend(_leaf_paths(item, path))
        else:
            leaves.append((path, item))
    return leaves


def _metadata_for(path: str, field_metadata: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    selected: Mapping[str, Any] | None = field_metadata.get(path)
    if selected is None:
        matches = [
            (pattern, meta)
            for pattern, meta in field_metadata.items()
            if "*" in pattern and fnmatch.fnmatchcase(path, pattern)
        ]
        if matches:
            selected = sorted(matches, key=lambda item: len(item[0]), reverse=True)[0][1]
    selected = selected or {}
    restart_required = bool(selected.get("restart_required", False))
    apply_mode = str(selected.get("apply_mode") or ("restart" if restart_required else "hot"))
    if apply_mode not in _APPLY_MODE_RANK:
        raise EffectiveConfigError("invalid_apply_mode", f"unsupported apply mode for {path}: {apply_mode!r}")
    if apply_mode == "restart":
        restart_required = True
    return {"apply_mode": apply_mode, "restart_required": restart_required}


def _merge_layer(
    target: dict[str, Any],
    patch: Mapping[str, Any],
    *,
    layer: str,
    selector: Mapping[str, str],
    provenance: dict[str, dict[str, Any]],
    field_metadata: Mapping[str, Mapping[str, Any]],
    prefix: str = "",
) -> None:
    for key in sorted(patch):
        path = f"{prefix}.{key}" if prefix else key
        value = patch[key]
        if value is None:
            continue
        current = target.get(key)
        if isinstance(value, Mapping):
            if current is None:
                current = {}
                target[key] = current
            if not isinstance(current, dict):
                current = {}
                target[key] = current
            if value:
                _merge_layer(
                    current,
                    value,
                    layer=layer,
                    selector=selector,
                    provenance=provenance,
                    field_metadata=field_metadata,
                    prefix=path,
                )
            else:
                target[key] = {}
                provenance[path] = {
                    "layer": layer,
                    "selector": dict(selector),
                    **_metadata_for(path, field_metadata),
                }
            continue
        target[key] = copy.deepcopy(value)
        provenance[path] = {
            "layer": layer,
            "selector": dict(selector),
            **_metadata_for(path, field_metadata),
        }


def resolve_effective_config(
    system_config: Mapping[str, Any],
    *,
    scope: RuntimeScope,
    bot_config: Mapping[str, Any] | None = None,
    session_config: Mapping[str, Any] | None = None,
    user_config: Mapping[str, Any] | None = None,
    relationship_config: Mapping[str, Any] | None = None,
    field_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> EffectiveConfigResult:
    """纯函数解析一个正式 Scope 的分层配置。"""
    scope = _require_scope(scope)
    metadata = _mapping(field_metadata or {}, field="field_metadata")
    base = _json_copy(_mapping(system_config, field="system_config"), field="system_config")
    provenance: dict[str, dict[str, Any]] = {}
    for path, _ in _leaf_paths(base):
        provenance[path] = {
            "layer": "system",
            "selector": {},
            **_metadata_for(path, metadata),
        }

    layer_values = (
        ("bot", bot_config),
        ("session", session_config),
        ("user", user_config),
        ("relationship", relationship_config),
    )
    applied: list[dict[str, Any]] = [{"layer": "system", "selector": {}}]
    for layer, raw_patch in layer_values:
        if raw_patch is None:
            continue
        patch = _json_copy(_mapping(raw_patch, field=f"{layer}_config"), field=f"{layer}_config")
        selector = scope_selector(layer, scope)
        _merge_layer(
            base,
            patch,
            layer=layer,
            selector=selector,
            provenance=provenance,
            field_metadata=metadata,
        )
        applied.append({"layer": layer, "selector": selector})

    restart_paths = tuple(sorted(path for path, item in provenance.items() if item.get("restart_required")))
    apply_mode = "hot"
    if provenance:
        apply_mode = max(
            (str(item.get("apply_mode") or "hot") for item in provenance.values()),
            key=lambda mode: _APPLY_MODE_RANK[mode],
        )
    scope_payload = scope.to_dict()
    revision_payload = {
        "values": base,
        "provenance": provenance,
        "scope": scope_payload,
    }
    revision = f"effective-{hashlib.sha256(_canonical(revision_payload).encode('utf-8')).hexdigest()[:20]}"
    return EffectiveConfigResult(
        values=base,
        provenance=provenance,
        revision=revision,
        apply_mode=apply_mode,
        restart_required=bool(restart_paths),
        restart_paths=restart_paths,
        applied_layers=tuple(applied),
        scope=scope_payload,
    )


def validate_layer_store(layers: Mapping[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    """完整校验持久化层级 store；任意非法层/entry 都整体拒绝。"""
    raw_layers = _mapping(layers or {}, field="layers")
    normalized: dict[str, list[dict[str, Any]]] = {name: [] for name in _SCOPED_LAYERS}
    seen_source_names: dict[str, str] = {}
    for source_layer, entries in raw_layers.items():
        layer = _normalize_layer(source_layer)
        if layer == "system":
            raise EffectiveConfigError("invalid_config_layer", "system overrides must use legacy top-level Channel_Settings fields")
        previous_source = seen_source_names.get(layer)
        if previous_source is not None and previous_source != source_layer:
            raise EffectiveConfigError("duplicate_config_layer", f"both {previous_source!r} and {source_layer!r} target {layer!r}")
        seen_source_names[layer] = source_layer
        if not isinstance(entries, list):
            raise EffectiveConfigError("invalid_layer_entries", f"layers.{source_layer} must be a list")
        seen_selectors: set[str] = set()
        for index, entry in enumerate(entries):
            item = _mapping(entry, field=f"layers.{source_layer}[{index}]")
            if set(item) != {"selector", "patch"}:
                raise EffectiveConfigError(
                    "invalid_layer_entry",
                    f"layers.{source_layer}[{index}] must contain exactly selector and patch",
                )
            selector = _validate_selector(layer, item.get("selector"))
            patch = _json_copy(_mapping(item.get("patch"), field=f"layers.{source_layer}[{index}].patch"), field="patch")
            selector_key = _canonical(selector)
            if selector_key in seen_selectors:
                raise EffectiveConfigError("duplicate_layer_selector", f"duplicate {layer} selector: {selector_key}")
            seen_selectors.add(selector_key)
            normalized[layer].append({"selector": selector, "patch": patch})
    return normalized


def patches_for_scope(layers: Mapping[str, Any] | None, scope: RuntimeScope) -> dict[str, dict[str, Any] | None]:
    """完整校验后，只返回与 exact RuntimeScope 匹配的各层 patch。"""
    scope = _require_scope(scope)
    validated = validate_layer_store(layers)
    selected: dict[str, dict[str, Any] | None] = {name: None for name in _SCOPED_LAYERS}
    for layer in LAYER_ORDER[1:]:
        try:
            expected = scope_selector(layer, scope)
        except EffectiveConfigError as exc:
            if exc.reason_code in {"session_scope_required", "scope_subject_required"}:
                continue
            raise
        matches = [entry for entry in validated[layer] if entry["selector"] == expected]
        if len(matches) > 1:
            raise EffectiveConfigError("ambiguous_layer_selector", f"multiple {layer} entries match the same Scope")
        if matches:
            selected[layer] = copy.deepcopy(matches[0]["patch"])
    return selected


def upsert_layer_patch(
    layers: Mapping[str, Any] | None,
    *,
    layer: str,
    scope: RuntimeScope,
    patch: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """纯函数更新一个 exact Scope 的层级 patch，并对整个候选 store 重新校验。"""
    normalized_layer = _normalize_layer(layer)
    if normalized_layer == "system":
        raise EffectiveConfigError("invalid_config_layer", "system patch is stored in legacy top-level fields")
    scope = _require_scope(scope)
    selector = scope_selector(normalized_layer, scope)
    validated = validate_layer_store(layers)
    patch_copy = _json_copy(_mapping(patch, field="patch"), field="patch")
    entries = [copy.deepcopy(entry) for entry in validated[normalized_layer] if entry["selector"] != selector]
    entries.append({"selector": selector, "patch": patch_copy})
    entries.sort(key=lambda item: _canonical(item["selector"]))
    validated[normalized_layer] = entries
    return validate_layer_store(validated)


__all__ = [
    "EffectiveConfigError",
    "EffectiveConfigResult",
    "LAYER_ORDER",
    "patches_for_scope",
    "resolve_effective_config",
    "scope_selector",
    "upsert_layer_patch",
    "validate_layer_store",
]
