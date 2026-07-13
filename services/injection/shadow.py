"""新注入编排器影子运行器。

影子模式只对比并记录 trace，不修改真实 ProviderRequest。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import Any, Iterable

from .channel_base import InjectionChannel
from .context import InjectionContext
from .orchestrator import InjectionOrchestrator, OrchestrationResult
from .trace_store import InjectionTraceStore, runtime_scope_metadata
from ..config.channel_config import ChannelConfigSet


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


__all__ = ["ShadowProviderRequest", "compare_injection_texts", "run_injection_shadow"]
