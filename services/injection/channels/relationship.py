"""正式 Scoped Relationship/affinity 注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
from ..channel_base import InjectionResult
from .safety import is_channel_allowed_in_mode

try:
    from ....domain.scope import RuntimeScope
except ImportError:  # pragma: no cover - focused repository tests
    from domain.scope import RuntimeScope


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("affinity", {}))


class RelationshipChannel:
    """Read only current Bot + group + sender relationship state."""

    name = "affinity"

    def __init__(self, *, repository: Any = None):
        self.repository = repository

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"affinity channel disabled in {mode} mode")
        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="affinity channel disabled by config")
        scope = getattr(ctx, "scope", None)
        if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
            return InjectionResult.empty(self.name, reason="runtime_scope_required")
        if not scope.subject_principal_id or self.repository is None:
            return InjectionResult.empty(self.name, reason="relationship_subject_or_repository_unavailable")
        try:
            state = self.repository.get_state(scope, subject_principal_id=scope.subject_principal_id, limit=25, offset=0)
            relationship = _mapping(_mapping(state).get("relationship"))
            if relationship.get("affinity") is None:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="relationship_unknown")
            dimensions = _mapping(relationship.get("dimensions"))
            values = _mapping(relationship.get("values"))
            labels = []
            for name in ("familiarity", "trust", "fun", "depth", "hostility"):
                item = _mapping(values.get(name))
                value = item.get("effective_value", dimensions.get(name))
                if value is not None:
                    labels.append(f"{name}={round(float(value), 1)}")
            text = (
                "[当前关系状态：仅用于调整对当前用户的自然回应，不代表必须改变事实或主动提及关系] "
                f"态度={relationship.get('state') or 'unknown'}，综合值={relationship.get('affinity')}"
            )
            if labels:
                text += "；" + "、".join(labels)
            history = _mapping(_mapping(state).get("relationship_history")).get("items") or []
            recent_events: list[str] = []
            for item in history[:3]:
                if not isinstance(item, Mapping):
                    continue
                reason = str(item.get("reason") or "").strip().replace("\n", " ")[:80]
                event_type = str(item.get("event_type") or "互动").strip()[:30]
                if reason and not is_identity_contamination(reason):
                    recent_events.append(f"{event_type}：{reason}")
            if recent_events:
                text += "\n最近互动线索（仅用于保持连续性）：" + "；".join(recent_events)
            timeline = _mapping(_mapping(state).get("timeline")).get("items") or []
            shared_events: list[str] = []
            for item in timeline[:2]:
                if not isinstance(item, Mapping):
                    continue
                summary = str(item.get("event_summary") or "").strip().replace("\n", " ")[:100]
                if summary and not is_identity_contamination(summary):
                    shared_events.append(summary)
            if shared_events:
                text += "\n与当前用户相关的近期共同经历：" + "；".join(shared_events)
            text = text[:900]
            if is_identity_contamination(text):
                result = InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="identity_contamination")
                result.filtered = [{"filter_reason": "identity_contamination", "filter_channel": self.name}]
                return result
            revision = relationship.get("revision") or _mapping(state).get("revision") or 0
            return InjectionResult.hit(
                self.name,
                text,
                items=[{
                    "source": "scoped_soul_relationship",
                    "subject_principal_id": scope.subject_principal_id,
                    "revision": revision,
                    "preview": text[:180],
                    "rendered_text": text,
                    "dedupe_key": f"relationship:{scope.bot_id}:{scope.session.id}:{scope.subject_principal_id}:{revision}",
                }],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["RelationshipChannel"]
