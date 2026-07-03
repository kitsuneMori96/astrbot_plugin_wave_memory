"""Agent 工具：基于 trace 证据提交通道配置建议。"""

from __future__ import annotations

import json
from dataclasses import field
from typing import Any

from pydantic.dataclasses import dataclass

try:
    from astrbot.core.agent.tool import FunctionTool
    from astrbot.core.agent.run_context import ContextWrapper
    from astrbot.core.astr_agent_context import AstrAgentContext
except Exception:  # pragma: no cover
    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    class FunctionTool(Generic[_T]):
        pass

    class ContextWrapper(Generic[_T]):
        pass

    class AstrAgentContext:
        pass

try:
    from services.agent.permission_policy import check_agent_action
    from services.config.channel_config import KNOWN_CHANNELS
    from services.injection.config_suggestion_store import ConfigSuggestionStore, VALID_CONFIG_PROBLEMS, VALID_SUGGESTION_SCOPES
    from services.injection.trace_store import InjectionTraceStore
except Exception:  # pragma: no cover
    from ..services.agent.permission_policy import check_agent_action
    from ..services.config.channel_config import KNOWN_CHANNELS
    from ..services.injection.config_suggestion_store import ConfigSuggestionStore, VALID_CONFIG_PROBLEMS, VALID_SUGGESTION_SCOPES
    from ..services.injection.trace_store import InjectionTraceStore


def _as_trace_ids(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        # 支持 JSON 数组或逗号分隔，兼容 LLM 工具调用差异。
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
                if isinstance(decoded, list):
                    return [str(item).strip() for item in decoded if str(item).strip()]
            except Exception:
                pass
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


@dataclass
class WaveMemorySuggestConfigTool(FunctionTool[AstrAgentContext]):
    """只提交配置建议，不直接应用。"""

    name: str = "wave_memory_suggest_config"
    description: str = (
        "基于一个或多个注入 trace 证据提交 WaveMemory 通道配置优化建议。"
        "建议进入待审状态，不会直接应用配置。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "scope": {"type": "string", "enum": ["channel", "global"], "description": "建议范围"},
            "channel": {"type": "string", "description": "scope=channel 时的通道名，如 memory/facts/timeline"},
            "problem": {
                "type": "string",
                "enum": ["noise", "slow", "too_much", "too_little", "wrong_source"],
                "description": "证据显示的问题类型",
            },
            "evidence_trace_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "支持该建议的 trace_id 列表，至少一个",
            },
            "suggestion": {"type": "string", "description": "建议内容或候选配置变更说明"},
        },
        "required": ["scope", "problem", "evidence_trace_ids"],
    })

    permission_action: str = "suggest_config"
    trace_store: Any = field(default=None, repr=False)
    suggestion_store: Any = field(default=None, repr=False)
    db: Any = field(default=None, repr=False)
    conn: Any = field(default=None, repr=False)

    def _conn(self):
        if self.conn:
            return self.conn
        if not self.db:
            return None
        if getattr(self.db, "closed", False):
            try:
                self.db.reopen()
            except Exception:
                return None
        return getattr(self.db, "conn", None)

    def _trace_store(self):
        if self.trace_store:
            return self.trace_store
        conn = self._conn()
        if not conn:
            return None
        store = InjectionTraceStore(conn)
        store.ensure_schema()
        self.trace_store = store
        return store

    def _suggestion_store(self):
        if self.suggestion_store:
            return self.suggestion_store
        conn = self._conn()
        if not conn:
            return None
        store = ConfigSuggestionStore(conn)
        store.ensure_schema()
        self.suggestion_store = store
        return store

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        scope = str(kwargs.get("scope", "") or "").strip().lower()
        channel = str(kwargs.get("channel", "") or "").strip()
        problem = str(kwargs.get("problem", "") or "").strip().lower()
        suggestion = str(kwargs.get("suggestion", "") or "").strip()
        evidence_trace_ids = _as_trace_ids(kwargs.get("evidence_trace_ids"))

        if scope not in VALID_SUGGESTION_SCOPES:
            return "scope 必须是 channel 或 global"
        if problem not in VALID_CONFIG_PROBLEMS:
            return "problem 必须是 noise/slow/too_much/too_little/wrong_source 之一"
        if scope == "channel":
            if not channel:
                return "scope=channel 时必须提供 channel"
            if channel not in KNOWN_CHANNELS:
                return f"未知通道：{channel}"
        else:
            channel = ""
        if not evidence_trace_ids:
            return "至少提供一个 evidence_trace_id 作为建议证据"

        trace_store = self._trace_store()
        suggestion_store = self._suggestion_store()
        if not trace_store or not suggestion_store:
            return "配置建议存储未初始化"

        missing = [trace_id for trace_id in evidence_trace_ids if not trace_store.get(trace_id)]
        if missing:
            return f"找不到证据 trace：{missing[0]}"

        suggestion_id = suggestion_store.create(
            scope=scope,
            channel=channel,
            problem=problem,
            suggestion=suggestion,
            evidence_trace_ids=evidence_trace_ids,
            actor="agent",
            metadata={"applied": False, "policy": "review_required"},
        )
        return json.dumps(
            {
                "status": "pending_review",
                "suggestion_id": suggestion_id,
                "scope": scope,
                "channel": channel,
                "problem": problem,
                "evidence_trace_ids": evidence_trace_ids,
                "applied": False,
            },
            ensure_ascii=False,
        )


__all__ = ["WaveMemorySuggestConfigTool"]
