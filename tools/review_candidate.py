"""Agent 工具：提交记忆相关审查候选。"""

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
    from services.injection.trace_store import InjectionTraceStore
    from services.review.candidate_store import ReviewCandidateStore, VALID_REVIEW_CANDIDATE_TYPES
except Exception:  # pragma: no cover
    from ..services.agent.permission_policy import check_agent_action
    from ..services.injection.trace_store import InjectionTraceStore
    from ..services.review.candidate_store import ReviewCandidateStore, VALID_REVIEW_CANDIDATE_TYPES


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
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
class WaveMemorySubmitReviewCandidateTool(FunctionTool[AstrAgentContext]):
    """提交 memory/fact/belief/style/jargon 候选进入人工审查队列。"""

    name: str = "wave_memory_submit_review_candidate"
    description: str = (
        "提交记忆相关候选进入审查队列，支持 memory/fact/belief/style/jargon。"
        "必须提供证据和原因；不会直接提升为正式信念、风格或黑话。"
    )
    parameters: dict = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["memory", "fact", "belief", "style", "jargon"],
                "description": "候选类型",
            },
            "content": {"type": "string", "description": "候选内容"},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "证据列表，支持 trace:<trace_id> 或 memory:<id>",
            },
            "reason": {"type": "string", "description": "提交原因"},
        },
        "required": ["type", "content", "evidence", "reason"],
    })

    permission_action: str = "submit_review_candidate"
    candidate_store: Any = field(default=None, repr=False)
    trace_store: Any = field(default=None, repr=False)
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

    def _candidate_store(self):
        if self.candidate_store:
            return self.candidate_store
        conn = self._conn()
        if not conn:
            return None
        store = ReviewCandidateStore(conn)
        store.ensure_schema()
        self.candidate_store = store
        return store

    def _memory_exists(self, memory_id: int) -> bool:
        conn = self._conn()
        if not conn:
            return False
        try:
            row = conn.execute("SELECT 1 FROM memories WHERE id=?", (int(memory_id),)).fetchone()
            return bool(row)
        except Exception:
            return False

    def _validate_evidence(self, evidence: list[str]) -> str:
        trace_store = self._trace_store()
        if not trace_store:
            return "注入 trace 存储未初始化"
        for item in evidence:
            if item.startswith("trace:"):
                trace_id = item.split(":", 1)[1].strip()
                if not trace_id or not trace_store.get(trace_id):
                    return f"找不到证据 trace：{trace_id or item}"
            elif item.startswith("memory:"):
                raw_id = item.split(":", 1)[1].strip()
                try:
                    memory_id = int(raw_id)
                except (TypeError, ValueError):
                    return f"无效证据 memory：{raw_id or item}"
                if not self._memory_exists(memory_id):
                    return f"找不到证据 memory：{memory_id}"
            else:
                # 兼容裸 trace_id / 裸 memory_id。
                if trace_store.get(item):
                    continue
                try:
                    memory_id = int(item)
                except (TypeError, ValueError):
                    return f"未知证据格式：{item}"
                if not self._memory_exists(memory_id):
                    return f"找不到证据 memory：{memory_id}"
        return ""

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        decision = check_agent_action(self.permission_action)
        if not decision.allowed:
            return decision.reason
        candidate_type = str(kwargs.get("type", kwargs.get("candidate_type", "")) or "").strip().lower()
        content = str(kwargs.get("content", "") or "").strip()
        evidence = _as_list(kwargs.get("evidence"))
        reason = str(kwargs.get("reason", "") or "").strip()

        if candidate_type not in VALID_REVIEW_CANDIDATE_TYPES:
            return "type 必须是 memory/fact/belief/style/jargon 之一"
        if not content:
            return "必须提供 content"
        if not evidence:
            return "必须提供 evidence"
        if not reason:
            return "必须提供 reason"

        store = self._candidate_store()
        if not store:
            return "审查候选存储未初始化"
        evidence_error = self._validate_evidence(evidence)
        if evidence_error:
            return evidence_error

        candidate_id = store.create(
            candidate_type=candidate_type,
            content=content,
            evidence=evidence,
            reason=reason,
            actor="agent",
            metadata={"promoted": False, "policy": "review_required"},
        )
        return json.dumps(
            {
                "status": "pending_review",
                "candidate_id": candidate_id,
                "candidate_type": candidate_type,
                "promoted": False,
            },
            ensure_ascii=False,
        )


__all__ = ["WaveMemorySubmitReviewCandidateTool"]
