"""Agent 工具：对一次注入命中的记忆记录反馈。"""

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

try:
    from services.agent.permission_policy import check_agent_action
    from services.injection.feedback_store import MemoryFeedbackStore, VALID_MEMORY_FEEDBACK
    from services.injection.trace_store import InjectionTraceStore
except Exception:  # pragma: no cover - AstrBot 包导入路径
    from ..services.agent.permission_policy import check_agent_action
    from ..services.injection.feedback_store import MemoryFeedbackStore, VALID_MEMORY_FEEDBACK
    from ..services.injection.trace_store import InjectionTraceStore


def _details(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _trace_hit_memory_ids(trace: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for channel in trace.get("channels", []) or []:
        details = _details(channel.get("details"))
        for item in details.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            value = item.get("id")
            try:
                if value is not None and str(value).strip() != "":
                    ids.add(int(value))
            except (TypeError, ValueError):
                continue
    return ids


@dataclass
class WaveMemoryFeedbackMemoryTool(FunctionTool[AstrAgentContext]):
    """记录注入记忆反馈；不会直接删除或改写记忆内容。"""

    name: str = "wave_memory_feedback_memory"
    description: str = (
        "对某次注入 trace 中命中的 memory_id 记录反馈：useful/useless/misleading/duplicate。"
        "只写反馈记录；misleading/duplicate 不会直接删除记忆。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string", "description": "注入 trace_id，作为反馈证据"},
            "memory_id": {"type": "integer", "description": "该 trace 命中的记忆 id"},
            "feedback": {
                "type": "string",
                "description": "反馈类型",
                "enum": ["useful", "useless", "misleading", "duplicate"],
            },
            "reason": {"type": "string", "description": "反馈原因，建议简短说明"},
        },
        "required": ["trace_id", "memory_id", "feedback"],
    })

    permission_action: str = "feedback_memory"
    trace_store: Any = field(default=None, repr=False)
    feedback_store: Any = field(default=None, repr=False)
    db: Any = field(default=None, repr=False)
    conn: Any = field(default=None, repr=False)
    auto_apply_useful: bool = True
    useful_boost: float = 0.03

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

    def _feedback_store(self):
        if self.feedback_store:
            return self.feedback_store
        conn = self._conn()
        if not conn:
            return None
        store = MemoryFeedbackStore(conn)
        store.ensure_schema()
        self.feedback_store = store
        return store

    def _apply_useful_boost(self, memory_id: int) -> bool:
        if not self.auto_apply_useful:
            return False
        conn = self._conn()
        if not conn:
            return False
        cur = conn.execute(
            "UPDATE memories SET importance = MIN(3.0, COALESCE(importance, 1.0) + ?) WHERE id = ?",
            (float(self.useful_boost), int(memory_id)),
        )
        conn.commit()
        return bool(getattr(cur, "rowcount", 0))

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        trace_id = str(kwargs.get("trace_id", "") or "").strip()
        feedback = str(kwargs.get("feedback", "") or "").strip().lower()
        reason = str(kwargs.get("reason", "") or "").strip()
        try:
            memory_id = int(kwargs.get("memory_id"))
        except (TypeError, ValueError):
            return "请提供有效 memory_id"

        if not trace_id:
            return "请提供 trace_id"
        if feedback not in VALID_MEMORY_FEEDBACK:
            return "feedback 必须是 useful/useless/misleading/duplicate 之一"

        trace_store = self._trace_store()
        feedback_store = self._feedback_store()
        if not trace_store or not feedback_store:
            return "反馈存储未初始化"

        trace = trace_store.get(trace_id)
        if not trace:
            return f"没有找到注入 trace：{trace_id}"
        if memory_id not in _trace_hit_memory_ids(trace):
            return f"memory_id={memory_id} 不在该 trace 的命中项中，不能作为本次注入反馈证据"

        soft_applied = False
        if feedback == "useful":
            soft_applied = self._apply_useful_boost(memory_id)

        feedback_id = feedback_store.record(
            trace_id=trace_id,
            memory_id=memory_id,
            feedback=feedback,
            reason=reason,
            actor="agent",
            metadata={
                "soft_applied": soft_applied,
                "policy": "useful_boost_only; no direct delete for misleading/duplicate",
            },
        )
        return json.dumps(
            {
                "status": "recorded",
                "feedback_id": feedback_id,
                "trace_id": trace_id,
                "memory_id": memory_id,
                "feedback": feedback,
                "reason": reason,
                "soft_applied": soft_applied,
            },
            ensure_ascii=False,
        )


__all__ = ["WaveMemoryFeedbackMemoryTool"]
