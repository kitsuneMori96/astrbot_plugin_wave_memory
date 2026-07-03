"""注入请求上下文。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InjectionContext:
    """一次 `inject_memory` 调用的只读上下文。

    通道只读取该上下文并返回 `InjectionResult`，不得直接修改 ProviderRequest。
    """

    event: Any
    req: Any
    message: str
    group_id: str | None
    sender_id: str
    sender_name: str
    bot_id: str
    bot_profile_id: str
    recent_context: list[str] = field(default_factory=list)
    mode: str = "full"
    config: dict[str, Any] = field(default_factory=dict)
    now: float = 0.0
    trace_id: str = ""
