"""新注入编排器影子运行器。

影子模式只对比并记录 trace，不修改真实 ProviderRequest。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Any, Iterable, Mapping

from .channel_base import InjectionChannel
from .context import InjectionContext
from .orchestrator import InjectionOrchestrator, OrchestrationResult
from .trace_store import InjectionTraceStore, runtime_scope_metadata
from ..config.channel_config import ChannelConfigSet, channel_config_revision


class ShadowProviderRequest:
    """ProviderRequest 的浅拷贝代理。

    `extra_user_content_parts` 使用独立 list，避免 Orchestrator 的 TextPart 写入污染真实请求；
    其他属性按需透传给原始请求，兼容 AstrBot ProviderRequest 的动态字段。
    """

    def __init__(self, original: Any):
        self._original = original
        self.system_prompt = getattr(original, "system_prompt", "")
        self.extra_user_content_parts = list(getattr(original, "extra_user_content_parts", []) or [])

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def compare_injection_texts(old_text: str | None, new_text: str | None) -> dict[str, Any]:
    """返回旧注入文本与新编排器文本的可审计差异摘要。"""

    old = str(old_text or "")
    new = str(new_text or "")
    normalized_old = "\n".join(line.rstrip() for line in old.strip().splitlines())
    normalized_new = "\n".join(line.rstrip() for line in new.strip().splitlines())
    return {
        "exact_match": old == new,
        "normalized_match": normalized_old == normalized_new,
        "old_chars": len(old),
        "new_chars": len(new),
        "char_delta": len(new) - len(old),
        "old_hash": _hash_text(old),
        "new_hash": _hash_text(new),
        "old_preview": old[:300],
        "new_preview": new[:300],
    }


def _trace_status(comparison: dict[str, Any], result: OrchestrationResult) -> str:
    if comparison.get("exact_match"):
        return "shadow_match"
    if not result.final_text and not comparison.get("old_chars"):
        return "shadow_empty"
    return "shadow_diff"


def _record_shadow_trace(
    *,
    trace_store: InjectionTraceStore | None,
    ctx: InjectionContext,
    result: OrchestrationResult,
    comparison: dict[str, Any],
) -> None:
    if not trace_store:
        return
    trace_store.safe_record(
        {
            "trace_id": ctx.trace_id,
            "timestamp": ctx.now or time.time(),
            "mode": ctx.mode,
            "group_id": ctx.group_id,
            "sender_id": ctx.sender_id,
            "sender_name": ctx.sender_name,
            "bot_id": ctx.bot_id,
            "bot_profile_id": ctx.bot_profile_id,
            "message": ctx.message,
            "final_text": result.final_text,
            "total_latency_ms": result.total_latency_ms,
            "total_tokens": sum(r.tokens for r in result.channel_results if r.status == "hit"),
            "total_chars": len(result.final_text),
            "status": _trace_status(comparison, result),
            "metadata": {
                **runtime_scope_metadata(ctx.scope),
                "shadow_mode": True,
                "shadow_comparison": comparison,
            },
        },
        result.channel_results,
    )


async def run_injection_shadow(
    *,
    ctx: InjectionContext,
    channels: Iterable[InjectionChannel],
    config: ChannelConfigSet,
    trace_store: InjectionTraceStore | None,
    old_text: str = "",
    text_part_factory: Any | None = None,
) -> OrchestrationResult:
    """在影子 ProviderRequest 上运行新 Orchestrator，并记录新旧输出差异。

    真实 `ctx.req` 不会被修改；新编排器追加的 TextPart 只进入 `ShadowProviderRequest`。
    """

    shadow_req = ShadowProviderRequest(ctx.req)
    shadow_ctx = replace(ctx, req=shadow_req)
    orchestrator = InjectionOrchestrator(
        channels=channels,
        config=config,
        trace_store=None,
        text_part_factory=text_part_factory,
    )

    started = time.perf_counter()
    try:
        result = await orchestrator.run(shadow_ctx)
    except Exception as exc:  # pragma: no cover - 编排器整体防御
        result = OrchestrationResult(
            trace_id=ctx.trace_id,
            injected=False,
            final_text="",
            channel_results=[],
            total_latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        if trace_store:
            comparison = compare_injection_texts(old_text, "")
            trace_store.safe_record(
                {
                    "trace_id": ctx.trace_id,
                    "timestamp": ctx.now or time.time(),
                    "mode": ctx.mode,
                    "group_id": ctx.group_id,
                    "sender_id": ctx.sender_id,
                    "sender_name": ctx.sender_name,
                    "bot_id": ctx.bot_id,
                    "bot_profile_id": ctx.bot_profile_id,
                    "message": ctx.message,
                    "final_text": "",
                    "total_latency_ms": result.total_latency_ms,
                    "total_tokens": 0,
                    "total_chars": 0,
                    "status": "shadow_error",
                    "error": str(exc),
                    "metadata": {
                        "shadow_mode": True,
                        "shadow_comparison": comparison,
                    },
                },
                [],
            )
        return result

    comparison = compare_injection_texts(old_text, result.final_text)
    if config.trace_enabled:
        _record_shadow_trace(trace_store=trace_store, ctx=ctx, result=result, comparison=comparison)
    return result


def _item_identity(channel: str, item: Mapping[str, Any], index: int) -> str:
    for key in ("id", "object_ref", "memory_id", "word", "key"):
        value = item.get(key)
        if value is not None and str(value) != "":
            return f"{channel}:{key}:{value}"
    return f"{channel}:index:{index}"


def _bounded_result_snapshot(result: OrchestrationResult, *, max_items: int) -> dict[str, Any]:
    channels: list[dict[str, Any]] = []
    ranking: list[str] = []
    filtered: list[dict[str, Any]] = []
    for channel_result in result.channel_results[:max_items]:
        items: list[dict[str, Any]] = []
        for index, raw_item in enumerate((channel_result.items or [])[:max_items]):
            item = raw_item if isinstance(raw_item, Mapping) else {}
            identity = _item_identity(channel_result.channel, item, index)
            ranking.append(identity)
            items.append({
                "key": identity,
                "score": item.get("score"),
                "similarity": item.get("similarity"),
            })
        for index, raw_item in enumerate((channel_result.filtered or [])[:max_items]):
            item = raw_item if isinstance(raw_item, Mapping) else {}
            filtered.append({
                "key": _item_identity(channel_result.channel, item, index),
                "channel": channel_result.channel,
                "reason": str(item.get("filter_reason") or item.get("reason") or "filtered")[:80],
            })
        channels.append({
            "channel": channel_result.channel,
            "status": channel_result.status,
            "item_count": len(channel_result.items or []),
            "filtered_count": len(channel_result.filtered or []),
            "items": items,
        })
    return {
        "injected": result.injected,
        "text_chars": len(result.final_text),
        "channels": channels,
        "ranking": ranking[:max_items],
        "filtered": filtered[:max_items],
        "truncated": any(
            len(channel_result.items or []) > max_items or len(channel_result.filtered or []) > max_items
            for channel_result in result.channel_results
        ) or len(result.channel_results) > max_items,
    }


def _diff_snapshots(current: Mapping[str, Any], candidate: Mapping[str, Any], *, max_items: int) -> dict[str, Any]:
    current_rank = list(current.get("ranking") or [])
    candidate_rank = list(candidate.get("ranking") or [])
    current_set, candidate_set = set(current_rank), set(candidate_rank)
    current_positions = {key: index + 1 for index, key in enumerate(current_rank)}
    candidate_positions = {key: index + 1 for index, key in enumerate(candidate_rank)}
    rank_changes = [
        {"key": key, "before": current_positions[key], "after": candidate_positions[key]}
        for key in sorted(current_set & candidate_set)
        if current_positions[key] != candidate_positions[key]
    ]
    current_status = {item["channel"]: item["status"] for item in current.get("channels") or []}
    candidate_status = {item["channel"]: item["status"] for item in candidate.get("channels") or []}
    status_changes = [
        {"channel": name, "before": current_status.get(name), "after": candidate_status.get(name)}
        for name in sorted(set(current_status) | set(candidate_status))
        if current_status.get(name) != candidate_status.get(name)
    ]
    current_filtered = {item["key"]: item["reason"] for item in current.get("filtered") or []}
    candidate_filtered = {item["key"]: item["reason"] for item in candidate.get("filtered") or []}
    filter_changes = [
        {"key": key, "before": current_filtered.get(key), "after": candidate_filtered.get(key)}
        for key in sorted(set(current_filtered) | set(candidate_filtered))
        if current_filtered.get(key) != candidate_filtered.get(key)
    ]
    return {
        "hits": {
            "added": [key for key in candidate_rank if key not in current_set][:max_items],
            "removed": [key for key in current_rank if key not in candidate_set][:max_items],
            "common_count": len(current_set & candidate_set),
        },
        "filter_changes": filter_changes[:max_items],
        "ranking_changes": rank_changes[:max_items],
        "channel_status_changes": status_changes[:max_items],
        "text_char_delta": int(candidate.get("text_chars") or 0) - int(current.get("text_chars") or 0),
        "truncated": any(len(items) > max_items for items in (rank_changes, status_changes, filter_changes)),
    }


def _preview_context(ctx: InjectionContext, config: ChannelConfigSet, *, collector: Any, revision: str, provenance: Mapping[str, Any]) -> InjectionContext:
    payload = dict(ctx.config or {})
    config_payload = config.to_dict()
    runtime_recall = dict(payload.get("memory_recall") or {})
    runtime_recall.update(config.memory_recall)
    payload.update(config_payload)
    payload["memory_recall"] = runtime_recall
    try:
        from engine.query_engine import QueryOptions
    except ImportError:  # pragma: no cover - AstrBot 包导入路径
        from ...engine.query_engine import QueryOptions
    return replace(
        ctx,
        req=ShadowProviderRequest(ctx.req),
        config=payload,
        channel_options=config_payload["channels"],
        query_options=QueryOptions(touch=False, stages=config.query_stages, params=config.query_params),
        query_collector=collector,
        dry_run=True,
        trace_id="",
        config_revision=revision,
        config_provenance=dict(provenance),
    )


async def run_config_impact_preview(
    *,
    ctx: InjectionContext,
    channels: Iterable[InjectionChannel],
    current_config: ChannelConfigSet,
    candidate_config: ChannelConfigSet,
    expected_scope: Any = None,
    current_revision: str = "",
    candidate_revision: str = "",
    current_provenance: Mapping[str, Any] | None = None,
    candidate_provenance: Mapping[str, Any] | None = None,
    max_items: int = 20,
) -> dict[str, Any]:
    """在同一正式 Scope 下执行 current/candidate dry-run，不写请求、Trace 或访问计数。"""
    try:
        from domain.scope import RuntimeScope
    except ImportError:  # pragma: no cover - AstrBot 包导入路径
        from ...domain.scope import RuntimeScope
    if not isinstance(ctx.scope, RuntimeScope) or ctx.scope.visibility != "group" or ctx.scope.session is None:
        return {"ok": False, "error_code": "canonical_group_scope_required", "errors": ["preview requires an exact group RuntimeScope"]}
    if expected_scope is not None and expected_scope != ctx.scope:
        return {"ok": False, "error_code": "cross_scope_preview_rejected", "errors": ["current and candidate preview Scope must match exactly"]}
    if ctx.bot_profile_id != ctx.scope.bot_id:
        return {"ok": False, "error_code": "cross_scope_preview_rejected", "errors": ["InjectionContext bot_profile_id does not match RuntimeScope.bot_id"]}

    max_items = max(1, min(int(max_items), 50))
    try:
        from engine.query_engine import QueryDebugCollector
    except ImportError:  # pragma: no cover - AstrBot 包导入路径
        from ...engine.query_engine import QueryDebugCollector
    current_collector = QueryDebugCollector(max_items_per_partition=max_items, max_total_bytes=16_000)
    candidate_collector = QueryDebugCollector(max_items_per_partition=max_items, max_total_bytes=16_000)
    current_revision = current_revision or channel_config_revision(current_config)
    candidate_revision = candidate_revision or channel_config_revision(candidate_config)

    async def run_one(config: ChannelConfigSet, collector: Any, revision: str, provenance: Mapping[str, Any]) -> OrchestrationResult:
        preview_ctx = _preview_context(ctx, config, collector=collector, revision=revision, provenance=provenance)
        orchestrator = InjectionOrchestrator(
            channels=channels,
            config=config,
            trace_store=None,
            text_part_factory=None,
        )
        return await orchestrator.run(preview_ctx)

    current_result = await run_one(current_config, current_collector, current_revision, current_provenance or {})
    candidate_result = await run_one(candidate_config, candidate_collector, candidate_revision, candidate_provenance or {})
    current_snapshot = _bounded_result_snapshot(current_result, max_items=max_items)
    candidate_snapshot = _bounded_result_snapshot(candidate_result, max_items=max_items)
    current_debug = current_collector.snapshot()
    candidate_debug = candidate_collector.snapshot()
    return {
        "ok": True,
        "dry_run": True,
        "scope": ctx.scope.to_dict(),
        "current": {
            "revision": current_revision,
            "provenance": dict(current_provenance or {}),
            "result": current_snapshot,
            "query": {
                "final_ids": list((current_debug.get("final") or {}).get("ids") or [])[:max_items],
                "warnings": list(current_debug.get("warnings") or [])[:max_items],
            },
        },
        "candidate": {
            "revision": candidate_revision,
            "provenance": dict(candidate_provenance or {}),
            "result": candidate_snapshot,
            "query": {
                "final_ids": list((candidate_debug.get("final") or {}).get("ids") or [])[:max_items],
                "warnings": list(candidate_debug.get("warnings") or [])[:max_items],
            },
        },
        "diff": _diff_snapshots(current_snapshot, candidate_snapshot, max_items=max_items),
        "limits": {"max_items": max_items, "max_query_debug_bytes": 16_000},
    }


__all__ = [
    "ShadowProviderRequest",
    "compare_injection_texts",
    "run_config_impact_preview",
    "run_injection_shadow",
]
