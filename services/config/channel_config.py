"""注入通道热配置模型。

把旧 `Query_Settings` / `Inject_Settings` 映射为通道级默认配置，并对热更新做防御性校验。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping


KNOWN_MODES = frozenset({"full", "memory_only", "compat_only"})
MAX_TIMEOUT_MS = 5000
KNOWN_CHANNELS = (
    "safety",
    "memory",
    "timeline",
    "facts",
    "persona",
    "belief",
    "jargon",
    "fewshot",
    "book_lore",
    "fts5",
    "affinity",
)

_ADVANCED_FULL_ONLY = {"persona", "belief", "jargon", "fewshot", "book_lore", "affinity"}
_OPTIONAL_MEMORY_ONLY = {"timeline", "facts", "fts5"}


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    enabled: bool
    priority: int
    top_k: int | None = None
    max_items: int | None = None
    token_budget: int = 300
    timeout_ms: int = 300
    min_score: float | None = None
    modes: tuple[str, ...] = ("full",)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["modes"] = list(self.modes)
        return payload


@dataclass(frozen=True)
class ChannelConfigSet:
    mode: str
    channels: dict[str, ChannelConfig]
    recent_dedup_minutes: int = 30
    trace_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "recent_dedup_minutes": self.recent_dedup_minutes,
            "trace_enabled": self.trace_enabled,
            "channels": {name: cfg.to_dict() for name, cfg in self.channels.items()},
        }


def _int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _modes_for(name: str, mode: str) -> tuple[str, ...]:
    if name == "safety":
        return ("full", "memory_only", "compat_only")
    if name == "memory":
        return ("full", "memory_only") if mode != "compat_only" else ("full", "memory_only", "compat_only")
    if name in _OPTIONAL_MEMORY_ONLY:
        return ("full", "memory_only")
    return ("full",)


def _enabled_for(name: str, mode: str, inject_cfg: Mapping[str, Any]) -> bool:
    if name == "safety":
        return True
    if mode == "compat_only":
        return False
    if mode == "memory_only" and name in _ADVANCED_FULL_ONLY:
        return False
    if name == "timeline":
        return _bool(inject_cfg.get("enable_timeline"), True) and _int(inject_cfg.get("timeline_max"), 5) > 0
    if name == "facts":
        return _int(inject_cfg.get("facts_max"), 5) > 0
    return True


def build_default_channel_config(
    *,
    runtime_mode: str = "full",
    query_cfg: Mapping[str, Any] | None = None,
    inject_cfg: Mapping[str, Any] | None = None,
) -> ChannelConfigSet:
    """从旧配置映射为模式感知的通道默认配置。"""
    mode = runtime_mode if runtime_mode in KNOWN_MODES else "full"
    query_cfg = query_cfg or {}
    inject_cfg = inject_cfg or {}
    inject_top_k = _int(query_cfg.get("inject_top_k"), 5)
    min_similarity = _float(query_cfg.get("min_similarity"), 0.45)
    facts_max = _int(inject_cfg.get("facts_max"), 5)
    timeline_max = _int(inject_cfg.get("timeline_max"), 5)
    recent_dedup = _int(inject_cfg.get("skip_recent_minutes"), 30)

    defaults = {
        "safety": ChannelConfig("safety", True, priority=1000, token_budget=0, timeout_ms=50, modes=_modes_for("safety", mode)),
        "memory": ChannelConfig("memory", _enabled_for("memory", mode, inject_cfg), priority=100, top_k=inject_top_k, token_budget=600, timeout_ms=2500, min_score=min_similarity, modes=_modes_for("memory", mode)),
        "timeline": ChannelConfig("timeline", _enabled_for("timeline", mode, inject_cfg), priority=80, max_items=timeline_max, token_budget=220, timeout_ms=120, modes=_modes_for("timeline", mode)),
        "facts": ChannelConfig("facts", _enabled_for("facts", mode, inject_cfg), priority=75, max_items=facts_max, token_budget=260, timeout_ms=120, modes=_modes_for("facts", mode)),
        "persona": ChannelConfig("persona", _enabled_for("persona", mode, inject_cfg), priority=70, max_items=1, token_budget=350, timeout_ms=2000, modes=_modes_for("persona", mode)),
        "belief": ChannelConfig("belief", _enabled_for("belief", mode, inject_cfg), priority=65, max_items=5, token_budget=220, timeout_ms=180, modes=_modes_for("belief", mode)),
        "jargon": ChannelConfig("jargon", _enabled_for("jargon", mode, inject_cfg), priority=60, max_items=3, token_budget=180, timeout_ms=160, modes=_modes_for("jargon", mode)),
        "fewshot": ChannelConfig("fewshot", _enabled_for("fewshot", mode, inject_cfg), priority=50, max_items=3, token_budget=260, timeout_ms=180, modes=_modes_for("fewshot", mode)),
        "book_lore": ChannelConfig("book_lore", _enabled_for("book_lore", mode, inject_cfg), priority=45, max_items=1, token_budget=260, timeout_ms=1500, min_score=0.35, modes=_modes_for("book_lore", mode)),
        "fts5": ChannelConfig("fts5", _enabled_for("fts5", mode, inject_cfg), priority=85, top_k=10, token_budget=350, timeout_ms=180, min_score=0.0, modes=_modes_for("fts5", mode)),
        "affinity": ChannelConfig("affinity", _enabled_for("affinity", mode, inject_cfg), priority=68, max_items=3, token_budget=180, timeout_ms=120, modes=_modes_for("affinity", mode)),
    }
    return ChannelConfigSet(mode=mode, channels=defaults, recent_dedup_minutes=recent_dedup, trace_enabled=True)


def _validate_channel_config(name: str, cfg: ChannelConfig) -> None:
    if name not in KNOWN_CHANNELS:
        raise ValueError(f"unknown channel: {name}")
    if name == "safety" and not cfg.enabled:
        raise ValueError("safety channel cannot be disabled while injection is enabled")
    if cfg.priority < 0:
        raise ValueError(f"{name}.priority must be non-negative")
    if cfg.top_k is not None and cfg.top_k < 0:
        raise ValueError(f"{name}.top_k must be non-negative")
    if cfg.max_items is not None and cfg.max_items < 0:
        raise ValueError(f"{name}.max_items must be non-negative")
    if cfg.token_budget < 0:
        raise ValueError(f"{name}.token_budget must be non-negative")
    if cfg.timeout_ms < 0 or cfg.timeout_ms > MAX_TIMEOUT_MS:
        raise ValueError(f"{name}.timeout_ms must be between 0 and {MAX_TIMEOUT_MS}")
    if cfg.min_score is not None and (cfg.min_score < 0 or cfg.min_score > 1):
        raise ValueError(f"{name}.min_score must be between 0 and 1")
    invalid_modes = [m for m in cfg.modes if m not in KNOWN_MODES]
    if invalid_modes:
        raise ValueError(f"{name}.modes contains unknown mode: {invalid_modes[0]}")


def apply_channel_overrides(base: ChannelConfigSet, overrides: Mapping[str, Any] | None) -> ChannelConfigSet:
    """校验并应用热配置覆盖；非法值不会部分生效。"""
    overrides = overrides or {}
    channels_override = overrides.get("channels", {}) or {}
    if not isinstance(channels_override, Mapping):
        raise ValueError("channels override must be an object")

    updated = dict(base.channels)
    for name, patch in channels_override.items():
        if name not in updated:
            raise ValueError(f"unknown channel: {name}")
        if not isinstance(patch, Mapping):
            raise ValueError(f"{name} override must be an object")
        current = updated[name]
        allowed = {"enabled", "priority", "top_k", "max_items", "token_budget", "timeout_ms", "min_score"}
        unknown_fields = [field for field in patch if field not in allowed]
        if unknown_fields:
            raise ValueError(f"unknown field for {name}: {unknown_fields[0]}")
        values: dict[str, Any] = {}
        for field, value in patch.items():
            if field == "enabled":
                values[field] = _bool(value, current.enabled)
            elif field in {"top_k", "max_items"}:
                values[field] = None if value is None or value == "" else _int(value, getattr(current, field) or 0)
            elif field in {"priority", "token_budget", "timeout_ms"}:
                values[field] = _int(value, getattr(current, field) or 0)
            elif field == "min_score":
                values[field] = None if value is None or value == "" else _float(value, current.min_score or 0.0)
        candidate = replace(current, **values)
        _validate_channel_config(name, candidate)
        updated[name] = candidate

    candidate_set = ChannelConfigSet(
        mode=base.mode,
        channels=updated,
        recent_dedup_minutes=_int(overrides.get("recent_dedup_minutes"), base.recent_dedup_minutes),
        trace_enabled=_bool(overrides.get("trace_enabled"), base.trace_enabled),
    )
    for name, cfg in candidate_set.channels.items():
        _validate_channel_config(name, cfg)
    if candidate_set.recent_dedup_minutes < 0:
        raise ValueError("recent_dedup_minutes must be non-negative")
    return candidate_set


def build_channel_config_from_plugin_config(plugin_config: Mapping[str, Any] | None) -> ChannelConfigSet:
    """从插件静态配置和保存的 Channel_Settings 覆盖生成有效通道配置。"""
    plugin_config = plugin_config or {}
    try:
        from ..runtime_mode import resolve_runtime_mode
        mode = resolve_runtime_mode(plugin_config).mode
    except Exception:
        mode = "full"
    base = build_default_channel_config(
        runtime_mode=mode,
        query_cfg=plugin_config.get("Query_Settings", {}) or {},
        inject_cfg=plugin_config.get("Inject_Settings", {}) or {},
    )
    return apply_channel_overrides(base, plugin_config.get("Channel_Settings", {}) or {})


def channel_config_diff(base: ChannelConfigSet, target: ChannelConfigSet) -> list[dict[str, Any]]:
    """返回 WebUI 可展示的通道配置差异。"""
    diffs: list[dict[str, Any]] = []

    def add(path: str, before: Any, after: Any) -> None:
        if before != after:
            diffs.append({"path": path, "before": before, "after": after})

    add("recent_dedup_minutes", base.recent_dedup_minutes, target.recent_dedup_minutes)
    add("trace_enabled", base.trace_enabled, target.trace_enabled)
    for name in KNOWN_CHANNELS:
        before = base.channels.get(name)
        after = target.channels.get(name)
        if not before or not after:
            continue
        for field in ("enabled", "priority", "top_k", "max_items", "token_budget", "timeout_ms", "min_score", "modes"):
            before_value = getattr(before, field)
            after_value = getattr(after, field)
            if isinstance(before_value, tuple):
                before_value = list(before_value)
            if isinstance(after_value, tuple):
                after_value = list(after_value)
            add(f"channels.{name}.{field}", before_value, after_value)
    return diffs
