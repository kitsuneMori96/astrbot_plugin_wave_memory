"""conversation_pipeline — 三段式对话架构（类 maibot，v5.0）。

流程：候选收集（硬触发 > 窗口候选 R1-R5 > ScenarioRegistry）
      → ConversationPlanner 单次 LLM（gate 判定 yes/no + 语气/详略/内心）
      → build_style_directive 注入 [风格指令]
      → Replayer = AstrBot 管线（唯一生成通道）。

本模块只含决策与风格产出，不做任何回复生成。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ─── 输出解析 ────────────────────────────────────────────────────

_TONES = ("热情", "正常", "冷淡", "克制")
_DETAILS = ("详细", "简洁")


def _line_field(line: str, *keys: str) -> Optional[str]:
    stripped = line.lstrip("-*# 　\t").replace("**", "").replace("`", "")
    for k in keys:
        for sep in ("：", ":"):
            if stripped.startswith(k + sep):
                return stripped[len(k) + 1:].strip()
    return None


def normalize_tone(raw: str) -> str:
    raw = (raw or "").strip()
    for t in _TONES:
        if t in raw:
            return t
    return "正常"


def normalize_detail(raw: str) -> str:
    raw = (raw or "").strip()
    for d in _DETAILS:
        if d in raw:
            return d
    return "简洁"


def parse_plan_response(text: str, *, no_reply_marker: str = "沉默") -> dict:
    """解析 Planner 输出：{reply, tone, detail, inner_thought}。"""
    result = {"reply": False, "tone": "正常", "detail": "简洁", "inner_thought": ""}
    if not text:
        return result
    action_raw = ""
    for raw_line in text.strip().split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        v = _line_field(line, "内心")
        if v is not None:
            result["inner_thought"] = v
            continue
        v = _line_field(line, "行动")
        if v is not None:
            action_raw = v
            continue
        v = _line_field(line, "语气")
        if v is not None:
            result["tone"] = normalize_tone(v)
            continue
        v = _line_field(line, "详略")
        if v is not None:
            result["detail"] = normalize_detail(v)
            continue
    result["reply"] = bool(action_raw) and no_reply_marker not in action_raw and "回复" in action_raw
    return result


# ─── 风格指令构建 ────────────────────────────────────────────────

def build_style_directive(prompt_service: Any, *, tone: str, detail: str,
                          inner_thought: str = "") -> str:
    """用 style_directive 模板构建 [风格指令]；无服务时退回固定文案。"""
    tone = normalize_tone(tone)
    detail = normalize_detail(detail)
    motivation = f"你想插话的动机：{inner_thought.strip()}" if (inner_thought or "").strip() else ""
    detail_rule = (
        "只输出一两句话，禁止展开解释、禁止列举、禁止超过 40 字"
        if detail == "简洁" else "最多一个自然段，不超过三句话"
    )
    if prompt_service is None:
        base = (f"[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}（{detail_rule}）。"
                "直接输出回复正文，不要任何前缀或分段编号。")
        return f"{base}{motivation}" if motivation else base
    try:
        return prompt_service.render(
            "style_directive",
            default="[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}。",
            tone=tone, detail=detail,
            motivation=(f" {motivation}" if motivation else ""),
        ).strip()
    except Exception as e:
        logger.warning(f"[ConversationPipeline] style directive render failed: {e}")
        return f"[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}。"


# ─── 特例场景注册表 ──────────────────────────────────────────────

@dataclass
class ScenarioHit:
    name: str
    prompt_hint: str


@dataclass
class Scenario:
    """一个主动对话特例场景：matcher 判定命中，quota 控制频率。

    require_engagement_signal=True 时，除关键词命中外还要求消息带对话关联信号
    （身份命中或与 bot 最近发言话题重叠）——裸句「你怎么还活着」光关键词命中
    不足以触发，否则会大量误回。
    """
    name: str
    matcher: Callable[[str], Any]                 # message -> 命中返回真值
    hint: str = ""                                # 注入 planner 的场景提示
    max_per_hour: int = 3                         # 0 = 不限
    interval_seconds: int = 0                     # 同群两次最小间隔
    enabled: bool = True
    require_engagement_signal: bool = True        # 求助类场景可关掉此门槛
    _last_ts: dict = field(default_factory=dict)   # {group_id: ts}
    _hourly: dict = field(default_factory=dict)    # {group_id: (hour, count)}

    def matches(self, message: str) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(self.matcher((message or "").strip()))
        except Exception as e:
            logger.debug(f"[ConversationPipeline] scenario {self.name} matcher error: {e}")
            return False

    def quota_ok(self, group_id: str) -> bool:
        now = time.time()
        if self.interval_seconds > 0 and now - self._last_ts.get(group_id, 0) < self.interval_seconds:
            return False
        if self.max_per_hour > 0:
            hour = int(now // 3600)
            hour_key, count = self._hourly.get(group_id, (hour, 0))
            if hour_key == hour and count >= self.max_per_hour:
                return False
        return True

    def record(self, group_id: str) -> None:
        now = time.time()
        self._last_ts[group_id] = now
        hour = int(now // 3600)
        hour_key, count = self._hourly.get(group_id, (hour, 0))
        self._hourly[group_id] = (hour, count + 1 if hour_key == hour else 1)

    def hit(self, message: str, group_id: str) -> Optional[ScenarioHit]:
        if self.matches(message) and self.quota_ok(group_id):
            return ScenarioHit(name=self.name, prompt_hint=self.hint)
        return None


_CUSTOM_LINE_RE = re.compile(r"^[^|]+(\|[^|]*){2,3}$")


def parse_custom_scenarios(text: str, *,
                           default_interval: int = 600) -> tuple[list[Scenario], list[str]]:
    """解析多行自定义场景：`名称|关键词,逗号分隔|语气提示|每小时上限`。

    返回 (scenarios, errors)；坏行跳过并收集错误信息。
    """
    scenarios, errors = [], []
    for idx, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            errors.append(f"第{idx}行格式错误（需 名称|关键词|提示[|上限]）：{line[:40]}")
            continue
        name, kw_text, hint = parts[0], parts[1], parts[2]
        try:
            max_per_hour = int(parts[3]) if len(parts) >= 4 and parts[3] else 3
        except ValueError:
            max_per_hour = 3
        keywords = [k for k in re.split(r"[,，;；]", kw_text) if k]
        if not name or not keywords:
            errors.append(f"第{idx}行缺少名称或关键词：{line[:40]}")
            continue
        lowered = [k.lower() for k in keywords]

        def _match(msg: str, kws=lowered) -> bool:
            m = msg[:500].lower()
            return any(k in m for k in kws)

        scenarios.append(Scenario(
            name=name,
            matcher=_match,
            hint=f"（触发场景：{name}。{hint}）" if hint else f"（触发场景：{name}）",
            max_per_hour=max(0, max_per_hour),
            interval_seconds=default_interval,
        ))
    return scenarios, errors


# ─── Planner ⊕ Player ───────────────────────────────────────────

class ConversationPlanner:
    """单次 LLM 完成「是否回复」判定与「语气/详略」风格产出。

    - plan_gate: 窗口候选 / 特例场景（输出 行动=回复/沉默）
    - plan_forced: @/私聊/引用（同模板家族去掉判定分支）
    """

    def __init__(self, llm: Any, prompt_service: Any = None,
                 context: Any = None, provider_ids: Optional[list[str]] = None):
        self.llm = llm
        self.prompt_service = prompt_service
        self.context = context

    def _resolve_persona_text(self, bot_id: str, group_id: str, bot_name: str) -> str:
        if self.prompt_service is not None:
            try:
                p = self.prompt_service.resolve_persona(bot_id=bot_id, group_id=group_id, bot_name=bot_name)
                if (p.get("system_prompt") or "").strip():
                    return p["system_prompt"]
            except Exception as e:
                logger.warning(f"[ConversationPipeline] resolve_persona failed: {e}")
        return f"当前身份：{bot_name or 'bot'}。保持自然、克制、有边界感。"

    def _persona_display_name(self, bot_id: str, group_id: str, bot_name: str) -> str:
        """人设显示名：优先提示词中心人设名（如『茉莉』），回退 registry 配置名。

        identity_guard 用 registry 名（可能与 wave 人设名不同）会造成双身份冲突。
        """
        if self.prompt_service is not None:
            try:
                p = self.prompt_service.resolve_persona(bot_id=bot_id, group_id=group_id, bot_name=bot_name)
                name = (p.get("name") or "").strip()
                if name and p.get("id"):
                    return name
            except Exception:
                pass
        return bot_name

    def _identity_guard(self, bot_name: str) -> str:
        if self.prompt_service is not None:
            try:
                guard = self.prompt_service.render_identity_guard(bot_name)
                if (guard or "").strip():
                    return guard
            except Exception:
                pass
        return ""

    @staticmethod
    def _format_context(context_messages: list[str], limit: int = 10) -> str:
        msgs = [m for m in (context_messages or []) if (m or "").strip()]
        return "\n".join(msgs[-limit:]) if msgs else "（无）"

    async def _call(self, prompt: str) -> str:
        from .identity_safety import prepend_identity_safety_system_prompt
        resp = await self.llm.text_chat(
            prompt=prompt,
            system_prompt=prepend_identity_safety_system_prompt(None, always=True),
            contexts=[],
        )
        return resp.completion_text or ""

    async def _plan(self, *, template_key: str, context_messages: list[str],
                    message: str, bot_id: str = "", group_id: str = "",
                    bot_name: str = "bot", scenario_hint: str = "",
                    at_hint: str = "", forced: bool = False) -> dict:
        ps = self.prompt_service
        persona = self._resolve_persona_text(bot_id, group_id, bot_name)
        display_name = self._persona_display_name(bot_id, group_id, bot_name) or bot_name
        guard = self._identity_guard(display_name)

        marker = "沉默"
        if ps is not None:
            marker = (ps.get_template("no_reply_marker") or "沉默").strip() or "沉默"

        variables = {
            "identity_guard": guard,
            "persona": persona,
            "context": self._format_context(context_messages),
            "message": (message or "").strip(),
            "scenario_hint": scenario_hint or "",
            "at_info": at_hint or "",
        }
        prompt = ps.render(template_key, **variables) if ps is not None else ""
        if not prompt:
            # 兜底：模板服务缺失时用极简内联结构
            prompt = (
                f"{guard}\n\n<self_persona>\n{persona}\n</self_persona>\n\n"
                f"【最近群聊】\n{self._format_context(context_messages)}\n\n"
                f"【消息】\n{variables['message']}\n"
                + ("对方正在直接和你说话。\n" if forced
                   else "请判断这条消息是否需要你回应。\n")
                + "输出（逐行）：\n内心：<一句话>\n"
                + ("" if forced else "行动：<回复 / 沉默>\n")
                + "语气：<热情 / 正常 / 冷淡 / 克制>\n详略：<详细 / 简洁>"
            )

        try:
            raw = await self._call(prompt)
        except Exception as e:
            logger.warning(f"[ConversationPipeline] plan ({template_key}) LLM failed: {e}")
            return {"reply": forced, "tone": "正常", "detail": "简洁", "inner_thought": ""}

        parsed = parse_plan_response(raw, no_reply_marker=marker)
        if forced:
            parsed["reply"] = True  # forced 路径无行动字段，必回
        return parsed

    async def plan_gate(self, *, context_messages: list[str], message: str,
                        bot_id: str = "", group_id: str = "", bot_name: str = "bot",
                        scenario_hint: str = "", at_hint: str = "") -> dict:
        """窗口候选 / 特例场景：完整判定（yes/no + 风格）。"""
        return await self._plan(
            template_key="planner_gate",
            context_messages=context_messages, message=message,
            bot_id=bot_id, group_id=group_id, bot_name=bot_name,
            scenario_hint=scenario_hint, at_hint=at_hint, forced=False,
        )

    async def plan_forced(self, *, context_messages: list[str], message: str,
                          bot_id: str = "", group_id: str = "",
                          bot_name: str = "bot") -> dict:
        """@/私聊/引用：跳过是否判定，仅产出风格（reply 恒 True）。"""
        return await self._plan(
            template_key="planner_forced",
            context_messages=context_messages, message=message,
            bot_id=bot_id, group_id=group_id, bot_name=bot_name,
            forced=True,
        )
