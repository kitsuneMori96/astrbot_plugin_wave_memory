"""主记忆召回注入通道。"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from ..channel_base import InjectionResult
from .safety import SafetyChannel, is_channel_allowed_in_mode

_DEFAULT_SOURCE_FILTER = ["core", "evolution", "experience", "lore", "bzz_experience", "bzz_evolution", "book_lore"]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _get_channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("memory", {}))


def _get_recall_cfg(ctx: Any) -> Mapping[str, Any]:
    return _mapping(_mapping(getattr(ctx, "config", {})).get("memory_recall", {}))


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _preview(text: str | None, limit: int = 120) -> str:
    compact = str(text or "").replace("\n", " ").strip()
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def _audit_item(memory: Mapping[str, Any]) -> dict[str, Any]:
    item = {
        "id": memory.get("id"),
        "source": memory.get("source", "live"),
        "score": memory.get("score"),
        "similarity": memory.get("similarity"),
        "preview": _preview(memory.get("content") or memory.get("summary") or ""),
    }
    if "timestamp" in memory:
        item["timestamp"] = memory.get("timestamp")
    return item


def _filter_recent_timestamp(memories: list[dict[str, Any]], ctx: Any, minutes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if minutes <= 0:
        return memories, []
    now = float(getattr(ctx, "now", 0.0) or 0.0)
    if now <= 0:
        return memories, []
    threshold = now - minutes * 60
    kept: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for memory in memories:
        ts = memory.get("timestamp")
        if isinstance(ts, (int, float)) and ts >= threshold:
            item = dict(memory)
            item["filter_reason"] = "recent_timestamp"
            item["filter_channel"] = "memory"
            filtered.append(item)
        else:
            kept.append(memory)
    return kept, filtered


class MemoryRecallChannel:
    """复用 QueryEngine 的主记忆召回通道。"""

    name = "memory"

    def __init__(self, *, query_engine: Any, safety_channel: SafetyChannel | None = None):
        self.query_engine = query_engine
        self.safety = safety_channel or SafetyChannel()

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"memory channel disabled in {mode} mode")

        channel_cfg = _get_channel_cfg(ctx)
        recall_cfg = _get_recall_cfg(ctx)
        top_k = _as_int(channel_cfg.get("top_k"), 5)
        if top_k <= 0:
            return InjectionResult.empty(self.name, reason="top_k is zero")

        try:
            memories = await self._query(ctx, top_k=top_k, recall_cfg=recall_cfg)
            memories = [dict(memory) for memory in memories or []]
            memories, recent_ts_filtered = _filter_recent_timestamp(
                memories,
                ctx,
                _as_int(recall_cfg.get("skip_recent_minutes"), _as_int(_mapping(getattr(ctx, "config", {})).get("recent_dedup_minutes"), 0)),
            )
            memories, safety_filtered = self.safety.filter_items(memories, ctx=ctx)
            filtered = [_audit_filtered(item) for item in [*recent_ts_filtered, *safety_filtered]]
            if not memories:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe memories")

            text = self.query_engine.format_injection(memories, current_group_id=getattr(ctx, "group_id", "") or "")
            if not text:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="formatted memory text is empty")
            return InjectionResult.hit(
                self.name,
                text,
                items=[_audit_item(memory) for memory in memories],
                filtered=filtered,
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    async def _query(self, ctx: Any, *, top_k: int, recall_cfg: Mapping[str, Any]) -> list[dict[str, Any]]:
        enable_shotgun = _as_bool(recall_cfg.get("enable_shotgun"), False)
        if enable_shotgun:
            context_messages = recall_cfg.get("context_messages") or getattr(ctx, "recent_context", []) or []
            return await self.query_engine.shotgun_query(
                text=getattr(ctx, "message", ""),
                context_messages=list(context_messages),
                group_id=getattr(ctx, "group_id", None),
                top_k=top_k,
                scope=getattr(ctx, "scope", None),
            )

        exclude_sources = recall_cfg.get("exclude_sources")
        source_filter = recall_cfg.get("source_filter")
        if source_filter is None and not exclude_sources:
            source_filter = list(_DEFAULT_SOURCE_FILTER)
        return await self.query_engine.query(
            text=getattr(ctx, "message", ""),
            group_id=getattr(ctx, "group_id", None),
            top_k=top_k,
            exclude_sources=exclude_sources,
            source_filter=source_filter,
            scope=getattr(ctx, "scope", None),
        )

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


def _audit_filtered(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = _audit_item(item)
    payload["filter_reason"] = item.get("filter_reason", "filtered")
    payload["filter_channel"] = item.get("filter_channel", "memory")
    return payload


__all__ = ["MemoryRecallChannel"]
