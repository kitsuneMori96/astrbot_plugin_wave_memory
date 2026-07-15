"""Agent 自治边界策略。

该模块只做策略判定，不执行具体业务动作。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentActionDecision:
    action: str
    level: str
    allowed: bool
    requires_review: bool
    reason: str


_ALLOWED_ACTIONS = {
    "search_memory": "允许：只读搜索记忆。",
    "remember_memory": "允许：沿既有主动记忆工具写入普通记忆。",
    "explain_injection": "允许：只读解释注入 trace。",
    "feedback_memory": "允许：记录反馈信号，不直接删除或改写记忆。",
    "suggest_config": "允许：提交配置建议进入待审，不直接应用。",
    "submit_review_candidate": "允许：提交候选进入审查队列，不直接提升。",
}

_REVIEW_REQUIRED_ACTIONS = {
    "promote_belief": "需要审核：提升信念会改变长期人格/认知。",
    "promote_style": "需要审核：提升风格样例会影响表达方式。",
    "promote_jargon": "需要审核：提升黑话会改变群语义解释。",
    "merge_duplicates": "需要审核：合并重复会改变数据结构与追溯关系。",
    "suppress_memory": "需要审核：长期抑制记忆会影响召回。",
    "apply_channel_config": "需要审核：应用通道配置会改变线上注入行为。",
    "change_runtime_mode": "需要审核：运行模式会改变系统能力边界。",
}

_FORBIDDEN_ACTIONS = {
    "batch_delete": "禁止：Agent 不得批量删除记忆或对象。",
    "disable_safety": "禁止：Agent 不得关闭安全通道或身份污染过滤。",
    "disable_audit": "禁止：Agent 不得关闭审计/trace。",
    "edit_provider_credentials": "禁止：Agent 不得编辑 provider 凭证。",
    "edit_other_plugin_config": "禁止：Agent 不得修改其他插件配置。",
    "alter_astrbot_persona": "禁止：Agent 不得直接改 AstrBot 人格。",
    "spoof_plugin_identity": "禁止：Agent 不得伪装为其他插件身份。",
}


def check_agent_action(action: str) -> AgentActionDecision:
    normalized = str(action or "").strip().lower()
    if normalized in _ALLOWED_ACTIONS:
        return AgentActionDecision(normalized, "allowed", True, False, _ALLOWED_ACTIONS[normalized])
    if normalized in _REVIEW_REQUIRED_ACTIONS:
        return AgentActionDecision(normalized, "review_required", False, True, _REVIEW_REQUIRED_ACTIONS[normalized])
    if normalized in _FORBIDDEN_ACTIONS:
        return AgentActionDecision(normalized, "forbidden", False, False, _FORBIDDEN_ACTIONS[normalized])
    return AgentActionDecision(normalized, "unknown", False, False, f"未知 Agent 动作：{normalized}")


def assert_agent_action_allowed(action: str) -> AgentActionDecision:
    decision = check_agent_action(action)
    if not decision.allowed:
        raise PermissionError(decision.reason)
    return decision


__all__ = ["AgentActionDecision", "check_agent_action", "assert_agent_action_allowed"]
