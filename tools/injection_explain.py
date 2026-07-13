"""Agent 工具：解释一次注入 trace。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

try:
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext
except Exception:  # pragma: no cover - 本地单测未安装 AstrBot SDK 时的轻量兜底
    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class FunctionTool(Generic[_T]):
        pass

    class ContextWrapper(Generic[_T]):
        pass

    class AstrAgentContext:
        pass

try:  # 兼容测试以 plugin root 为 sys.path 的导入方式
    from services.agent.permission_policy import check_agent_action
    from services.injection.trace_store import InjectionTraceStore
    from tools.scope_boundary import require_group_runtime_scope, scope_error_message
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ..services.agent.permission_policy import check_agent_action
    from ..services.injection.trace_store import InjectionTraceStore
    from .scope_boundary import require_group_runtime_scope, scope_error_message


def _parse_details(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _brief_hit(item: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": item.get("id") or item.get("rowid") or item.get("community_id") or item.get("example_id"),
        "source": item.get("source", ""),
        "score": item.get("score") or item.get("confidence") or item.get("effective_confidence"),
        "preview": item.get("preview", ""),
    }
    for key in ("word", "meaning", "source_layer", "reference_only", "runtime_match", "matched_by"):
        if key in item:
            payload[key] = item.get(key)
    return payload


def _brief_filtered(item: dict[str, Any]) -> dict[str, Any]:
    payload = _brief_hit(item)
    payload["reason"] = item.get("filter_reason", "filtered")
    payload["filter_channel"] = item.get("filter_channel", "")
    return payload


def build_injection_explanation(trace: dict[str, Any]) -> dict[str, Any]:
    """把 trace_store.get() 结果压缩成 Agent 可读的结构化解释。"""

    channels = []
    for channel in trace.get("channels", []) or []:
        details = _parse_details(channel.get("details"))
        items = [item for item in details.get("items", []) if isinstance(item, dict)]
        filtered = [item for item in details.get("filtered", []) if isinstance(item, dict)]
        channels.append({
            "channel": channel.get("channel", "unknown"),
            "status": channel.get("status", "empty"),
            "tokens": channel.get("tokens", 0),
            "chars": channel.get("chars", 0),
            "latency_ms": channel.get("latency_ms", 0),
            "score": channel.get("score"),
            "preview": channel.get("preview", ""),
            "hit_count": channel.get("item_count", len(items)),
            "filtered_count": channel.get("filtered_count", len(filtered)),
            "hit_items": [_brief_hit(item) for item in items[:8]],
            "filtered_items": [_brief_filtered(item) for item in filtered[:8]],
            "warnings": details.get("warnings", []) or [],
            "error": details.get("error", "") or "",
        })

    return {
        "trace_id": trace.get("trace_id", ""),
        "status": trace.get("status", ""),
        "mode": trace.get("mode", ""),
        "request": {
            "group_id": trace.get("group_id"),
            "sender_id": trace.get("sender_id"),
            "sender_name": trace.get("sender_name"),
            "bot_id": trace.get("bot_id"),
            "bot_profile_id": trace.get("bot_profile_id"),
            "message_preview": trace.get("message_preview", ""),
        },
        "budget": {
            "total_tokens": trace.get("total_tokens", 0),
            "total_chars": trace.get("total_chars", 0),
            "total_latency_ms": trace.get("total_latency_ms", 0),
        },
        "final_preview": trace.get("final_preview", ""),
        "channels": channels,
    }


@dataclass
class WaveMemoryExplainInjectionTool(FunctionTool[AstrAgentContext]):
    """解释一次 WaveMemory 注入 trace，便于 Agent 理解自己被哪些记忆影响。"""

    name: str = "wave_memory_explain_injection"
    description: str = "根据 trace_id 解释一次 WaveMemory 注入：各通道命中、过滤原因、预算、耗时和最终注入预览。只读，不修改任何记忆。"
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "trace_id": {
                "type": "string",
                "description": "要解释的注入 trace_id，可从日志或 Inject Observatory 获取",
            },
        },
        "required": ["trace_id"],
    })

    permission_action: str = "explain_injection"
    trace_store: Any = field(default=None, repr=False)
    db: Any = field(default=None, repr=False)

    def _get_trace_store(self):
        if self.trace_store:
            return self.trace_store
        if not self.db:
            return None
        if getattr(self.db, "closed", False):
            try:
                self.db.reopen()
            except Exception:
                return None
        try:
            store = InjectionTraceStore(self.db.conn)
            store.ensure_schema()
            self.trace_store = store
            return store
        except Exception:
            return None

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        trace_id = str(kwargs.get("trace_id", "") or "").strip()
        if not trace_id:
            return "请提供 trace_id"

        scope, error_code = require_group_runtime_scope(context, "injection.trace.read")
        if error_code:
            return scope_error_message("注入 trace 查询", error_code)
        assert scope is not None

        store = self._get_trace_store()
        if not store:
            return "注入 trace 存储未初始化"

        trace = store.get_for_scope(trace_id, scope)
        if not trace:
            return f"没有找到当前作用域内可验证的注入 trace：{trace_id}"

        return json.dumps(build_injection_explanation(trace), ensure_ascii=False, indent=2)


__all__ = ["WaveMemoryExplainInjectionTool", "build_injection_explanation"]
