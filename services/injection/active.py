"""新注入编排器主动运行器。"""

from __future__ import annotations

from typing import Any, Iterable

from .channel_base import InjectionChannel
from .context import InjectionContext
from .orchestrator import InjectionOrchestrator, OrchestrationResult
from .trace_store import InjectionTraceStore
from ..config.channel_config import ChannelConfigSet


async def run_injection_active(
    *,
    ctx: InjectionContext,
    channels: Iterable[InjectionChannel],
    config: ChannelConfigSet,
    trace_store: InjectionTraceStore | None,
    text_part_factory: Any | None = None,
) -> OrchestrationResult:
    """运行主动模式：Orchestrator 直接向真实 ProviderRequest 追加最终 TextPart。"""

    orchestrator = InjectionOrchestrator(
        channels=channels,
        config=config,
        trace_store=trace_store,
        text_part_factory=text_part_factory,
    )
    return await orchestrator.run(ctx)


__all__ = ["run_injection_active"]
