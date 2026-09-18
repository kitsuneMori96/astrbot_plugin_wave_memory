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

PLANNER_FIELD_GUIDE = """字段说明：
- 行动：只在确认对方在跟你说话时选回复。拿不准选沉默。
- 把握：你对「行动」判断的确定程度。高=上下文明确；中=上下文不全但仍可推断；低=信息不足、基本靠猜。
- 语气：热情=对方正常交流/求助/闲聊的常态；正常=事务性沟通、对方语气平淡、或已连续多轮降温；冷淡=对方明显低落/生气/想独处；克制=话题沉重、不宜外放。默认热情，无需刻意偏离。
- 详略：简洁=一句话能说清（是非判断、寒暄、简单确认、短观点）；详细=需要步骤/列举/解释原理/多个要点，或对方明确要求展开，或是评价/总结/分析类，或是 how-to/操作指导类问题。默认简洁。
- 念头：最后写。用你自己的口吻说出此刻最想表达的那一点。写意图或态度，不写事实复述。
  好：得赶紧给他说明白 / 这事儿我得先接住他的情绪 / 我想逗他一下
  差：他问我怎么部署 / 用户在求助 / 对方说了一句闲聊"""

# ─── 输出解析 ────────────────────────────────────────────────────

_TONES = ("热情", "正常", "冷淡", "克制")
_DETAILS = ("详细", "简洁")
_NEG = ("沉默", "不回复", "不回应", "无需回复", "跳过", "不答复", "不回")
_POS = ("回复", "回应")


def _norm(s: str) -> str:
    """归一化：全角→半角、去空白、去 markdown/序号符号。"""
    s = s.replace("：", ":").replace("　", "").replace(" ", "")
    return re.sub(r"[*`#>]+|\d+\.", "", s)


def _field(line: str, key: str) -> Optional[str]:
    """从 'key:value' 格式的行中提取值。"""
    line = _norm(line)
    m = re.match(rf"^{re.escape(key)}[:：](.*)$", line)
    return m.group(1).strip() if m else None


def normalize_tone(raw: str) -> str:
    raw = (raw or "").strip()
    for t in _TONES:
        if t in raw:
            return t
    return "热情"


def normalize_detail(raw: str) -> str:
    raw = (raw or "").strip()
    for d in _DETAILS:
        if d in raw:
            return d
    return "简洁"


def parse_plan_response(text: str, *, default_reply: bool = False) -> dict:
    """解析 Planner 输出：5 行字段格式。

    Returns: {reply, tone, detail, inner_thought, confidence, _miss}
    """
    result: dict[str, Any] = {
        "reply": default_reply,
        "tone": "热情",
        "detail": "简洁",
        "inner_thought": "",
        "confidence": "中",
        "_miss": [],
    }
    if not text:
        return result

    action_raw = ""
    for raw_line in text.strip().split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line and "：" not in line:
            continue
        for key, dst in (("行动", "action"), ("把握", "conf"),
                         ("语气", "tone"), ("详略", "detail"), ("念头", "thought")):
            v = _field(line, key)
            if v is None:
                continue
            if dst == "action":
                action_raw = v
            elif dst == "conf":
                result["confidence"] = v if v in ("高", "中", "低") else "中"
            elif dst == "thought":
                result["inner_thought"] = v
            elif dst == "tone":
                result["tone"] = v if v in _TONES else "热情"
            elif dst == "detail":
                result["detail"] = v if v in _DETAILS else "简洁"
            break

    # 否定优先（修 P0-1：「不回复」不再被判成回复）
    if any(n in action_raw for n in _NEG):
        result["reply"] = False
    elif any(p in action_raw for p in _POS):
        result["reply"] = True
    else:
        result["reply"] = default_reply

    if not result["inner_thought"]:
        result["_miss"].append("inner_thought")
    return result


# ─── 风格指令构建 ────────────────────────────────────────────────

def build_style_directive(prompt_service: Any, *, tone: str, detail: str,
                          inner_thought: str = "", confidence: str = "") -> str:
    """用 style_directive 模板构建 [风格指令]；无服务时退回固定文案。

    末尾恒定追加风格锚：人设底色逐回合强化，tone 只调强度不改底色——
    静态 system 人设会在长对话中权重衰减，靠每回合提醒维持风格。
    """
    tone = normalize_tone(tone)
    detail = normalize_detail(detail)
    cue = (
        f"\n<opening_cue>\n开口前你的念头：{inner_thought.strip()}\n</opening_cue>\n"
        if (inner_thought or "").strip()
        else ""
    )
    detail_rule = (
        "只输出一两句话，禁止展开解释、禁止列举、禁止超过 40 字"
        if detail == "简洁" else "最多一个自然段，不超过三句话"
    )
    intensity = {
        "热情": "全开",
        "正常": "自然释放",
        "冷淡": "收着但保持短句节奏",
        "克制": "收着但保持短句节奏",
    }.get(tone, "自然释放")
    anchor = (
        "\n[表达底色·始终生效] 元气直率、短句连发；明亮感靠语气词（吧/呢/哦/哟）和～承担，"
        "感叹号只属于干脆应答、慌张惊呼、真生气三种场合；情绪上头就拖长音——。"
        f"本次语气「{tone}」= 彩味{intensity}，只调强度不改底色。"
    )
    if prompt_service is None:
        base = (f"[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}（{detail_rule}）。"
                "直接输出回复正文，不要任何前缀或分段编号。")
        out = f"{base}{anchor}{cue}" if cue else f"{base}{anchor}"
        return out
    try:
        rendered = prompt_service.render(
            "style_directive",
            default="[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}。",
            tone=tone, detail=detail,
            cue=cue,
        ).strip()
        return f"{rendered}{anchor}"
    except Exception as e:
        logger.warning(f"[ConversationPipeline] style directive render failed: {e}")
        return f"[回复格式硬性要求] 本次回复：语气{tone}；篇幅{detail}。{anchor}"


# ─── 特例场景注册表 ──────────────────────────────────────────────

@dataclass
class ScenarioHit:
    name: str
    prompt_hint: str
    require_engagement_signal: bool = True


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
            return ScenarioHit(
                name=self.name,
                prompt_hint=self.hint,
                require_engagement_signal=self.require_engagement_signal,
            )
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

    async def _call(self, prompt: str, max_tokens: int = None) -> str:
        from .identity_safety import prepend_identity_safety_system_prompt
        resp = await self.llm.text_chat(
            prompt=prompt,
            system_prompt=prepend_identity_safety_system_prompt(None, always=True),
            contexts=[],
            max_tokens=max_tokens,
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

        variables = {
            "identity_guard": guard,
            "persona": persona,
            "context": self._format_context(context_messages),
            "message": (message or "").strip(),
            "scenario_hint": scenario_hint or "",
            "at_info": at_hint or "",
            "field_guide": PLANNER_FIELD_GUIDE,
        }
        prompt = ps.render(template_key, **variables) if ps is not None else ""
        if not prompt:
            # 兜底：模板服务缺失时用极简内联结构
            action_line = "行动：回复\n" if forced else "行动：<回复 / 沉默>\n"
            prompt = (
                f"{guard}\n\n<self_persona>\n{persona}\n</self_persona>\n\n"
                f"【最近群聊】\n{self._format_context(context_messages)}\n\n"
                f"【消息】\n{variables['message']}\n\n"
                + ("对方正在直接和你说话。\n" if forced
                   else "请判断这条消息是否需要你回应。\n")
                + "【输出格式】\n严格按以下 5 行输出：\n\n"
                + action_line
                + "把握：<高 / 中 / 低>\n"
                + "语气：<热情 / 正常 / 冷淡 / 克制>\n"
                + "详略：<详细 / 简洁>\n"
                + "念头：<开口前的一句内心话，第一人称，10-25字>"
            )

        try:
            raw = await self._call(prompt, max_tokens=200)
        except Exception as e:
            logger.warning(f"[ConversationPipeline] plan ({template_key}) LLM failed: {e}")
            return {"reply": forced, "tone": "热情", "detail": "简洁",
                    "inner_thought": "", "confidence": "中"}

        parsed = parse_plan_response(raw, default_reply=forced)
        if forced:
            parsed["reply"] = True  # forced 路径固定回复
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
