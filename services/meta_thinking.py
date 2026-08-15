"""MetaThinking — 羽书的内心判断层

每次被 @ 或抽样群消息时，先"想一下"再决定行为。
替代 response_gate 的硬规则门控。
"""

import json
import re
import time
from typing import Optional, Any

import logging; logger = logging.getLogger("wavememory")

from .llm_fallback import LLMFallbackClient, build_provider_chain, parse_provider_ids, provider_ids_from_config
from .identity_safety import is_identity_contamination, prepend_identity_safety_system_prompt


# 兜底硬规则
EXTREME_ATTACK = re.compile(r'(你[妈马]|nmsl|死[全妈]|全家|操你|fuck\s*you|滚去死|杀了你|弄死你)')


# ─── 回话后窗口内：粗筛"是否值得交给 LLM 自判主动回答"（纯函数）───
_WINDOW_CMD_PREFIXES = ("/teach", "/teach:", "记住", "记下", "remember", "忘记", "忘掉", "forget", "别记")
_WINDOW_ASK_RE = re.compile(r"什么|怎么|为啥|为什么|在哪|干啥|干嘛")
_WINDOW_COMPLAIN_RE = re.compile(r"不理我|别装死|人呢|回话|说话啊|无视我|咋不|怎么不说话|出来一下|在吗")
_WINDOW_IMPERATIVE_RE = re.compile(r"^(去|请|帮我|给我|搜索|查|找|来|喊|叫|给)")


def window_analysis_candidate(
    message: str,
    *,
    topic_overlap: float,
    identity_hit: bool,
    reply_ts: float = 0.0,
    now: float = None,
    aba_window: float = 30.0,
    overlap_threshold: float = 0.12,
    per_min: int = 3,
    count_state: dict = None,
) -> bool:
    """回话后窗口内粗筛候选消息。

    R1 问句 / R2 抱怨或呼唤 / R3 身份或引用命中 / R4 话题重叠 / R5 我向祈使。
    R5 需要发送者是 bot 刚互动过的对象（reply_ts 在 aba_window 内）。
    命中即交给 LLM 自判是否主动回答。count_state 就地累计频率上限。
    """
    if now is None:
        now = time.time()
    msg = (message or "").strip()
    if len(msg) < 2:
        return False
    for p in _WINDOW_CMD_PREFIXES:
        if msg.startswith(p):
            return False

    minute = int(now // 60)
    if count_state is None:
        count_state = {}
    if count_state.get("minute") != minute:
        count_state["minute"], count_state["count"] = minute, 0
    if count_state["count"] >= per_min:
        return False

    candidate = False
    if msg.endswith(("？", "?", "吗", "呢")) or _WINDOW_ASK_RE.search(msg):
        candidate = True                                  # R1 问句
    elif _WINDOW_COMPLAIN_RE.search(msg):
        candidate = True                                  # R2 抱怨/呼唤
    elif identity_hit:
        candidate = True                                  # R3 身份/引用
    elif topic_overlap >= overlap_threshold:
        candidate = True                                  # R4 话题重叠
    else:
        if now - reply_ts < aba_window and _WINDOW_IMPERATIVE_RE.match(msg):
            candidate = True                              # R5 我向祈使

    if candidate:
        count_state["count"] += 1
    return candidate


# ─── 求助检测（纯函数）：判定消息是否是求助，尤其编程提问 ───
_HELP_ASK_SIGNALS = (
    "求助", "救救", "帮帮我", "帮忙", "求教", "请教", "请问", "求问",
    "怎么做", "怎么实现", "怎么写", "怎么用", "怎么搞", "怎么改", "怎么办",
    "如何做", "如何实现", "如何写", "如何用", "咋办", "咋搞", "在线等",
    "求解决", "有人会吗", "有人知道吗", "有会吗", "教教我", "指点",
    "报错", "报异常", "出错了", "出bug", "异常了", "吓死了", "help", "bug",
)
_HELP_ASK_RE = re.compile(
    r"怎么|如何|咋|帮|教|请问|求助|不会|码一下|代码报错|报错|error|exception|traceback|failed|崩溃|crash|失败|不过|不行|挂了|坏了"
)
# 编程消息上的失败/异常信号：命中编程关键词后再配合此判断
_PROG_FAIL_RE = re.compile(r"报错|出错|失败|不过|不行|不了|挂了|坏了|crash|error|exception|traceback|failed")
_PROG_KEYWORDS = frozenset([
    "python", "js", "javascript", "typescript", "ts", "java", "golang", "go",
    "c++", "c#", "rust", "php", "vue", "react", "node", "npm", "pnpm", "yarn",
    "git", "docker", "linux", "机器", "前端", "后端", "代码", "脚本", "程序",
    "接口", "api", "sql", "数据库", "编译", "部署", "服务器", "运行报错",
    "env", "pip", "conda", "import", "shell", "bash",
])


def classify_help_request(message: str) -> str:
    """判定消息是否是求助，返回类型：'program' / 'general' / ''（非求助）。

    求助信号（问句/求助词/报错词）命中后，若含编程关键词则归为 program。
    此函数保持轻量（正则 + 集合匹配），不调用 LLM，供热路径预筛。
    """
    msg = (message or "").strip()
    if len(msg) < 2:
        return ""
    msg_lower = msg[:500].lower()
    if any(kw in msg_lower for kw in _PROG_KEYWORDS) and _PROG_FAIL_RE.search(msg_lower):
        return "program"
    for kw in _HELP_ASK_SIGNALS:
        if kw in msg_lower:
            return _prog_kind(msg_lower)
    if _HELP_ASK_RE.search(msg_lower):
        return _prog_kind(msg_lower)
    return ""


def _prog_kind(msg_lower: str) -> str:
    for kw in _PROG_KEYWORDS:
        if kw in msg_lower:
            return "program"
    return "general"


def parse_help_response(text: str) -> dict:
    """解析求助答疑 LLM 判断输出（纯函数）。"""
    result = {
        "action": "不答",
        "inner_thought": "",
        "need_web_search": False,
        "web_query": "",
    }
    for line in (text or "").strip().split("\n"):
        line = line.strip()
        if line.startswith("内心：") or line.startswith("内心:"):
            result["inner_thought"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("行动：") or line.startswith("行动:"):
            action = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if "答疑" in action or "解答" in action or ("答" in action and "不" not in action):
                result["action"] = "主动答疑"
        elif line.startswith("是否需要联网：") or line.startswith("是否需要联网:"):
            v = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            result["need_web_search"] = ("是" in v) or ("需" in v) or ("yes" in v.lower())
        elif line.startswith("搜索关键词：") or line.startswith("搜索关键词:"):
            v = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            if v and v != "无":
                result["web_query"] = v
    return result


class MetaThinking:
    """配置驱动的内心判断层 — 支持多 bot 身份。"""

    # 通用兴趣词（所有 bot 通用）
    _BASE_INTERESTS = frozenset(['好感度'])

    # 过滤掉的泛化标签（不作为触发器）
    BORING_TAGS = frozenset([
        '群内互动', '用户互动', '群友互怼', '群内冲突', '群内玩梗',
        '人身攻击', '日常闲聊', '闲聊', '灌水',
    ])

    def __init__(
        self,
        db,
        context,
        bot_qq_id: str = "",
        bot_qq_ids: list[str] = None,
        bot_prompts: dict[str, str] = None,
        bot_names: dict[str, str] = None,
        bot_db_ids: dict[str, str] = None,
        admin_ids: list[str] = None,
        config: dict | None = None,
        global_fallback_ids: str | list[str] | None = None,
        extra_interests: list[str] = None,
    ):
        self.db = db
        self.context = context
        self.bot_qq_id = bot_qq_id
        self.bot_qq_ids = set(bot_qq_ids or [bot_qq_id]) - {""}
        # 每个 bot 可以有自己的 MetaThinking prompt；没设置的用默认
        self.bot_prompts = bot_prompts or {}
        # bot_id → 显示名映射（用于生成回复时的身份选择）
        self.bot_names = bot_names or {}
        # bot_qq_id → db_id 映射（用于数据库 user_profiles.bot_id 写入）
        self.bot_db_ids = bot_db_ids or {bid: name.lower() for bid, name in self.bot_names.items()}
        self.admin_ids = set(admin_ids or [])
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.proactive_enabled = bool(self.config.get("proactive_enabled", True))
        self.spam_threshold = int(self.config.get("spam_threshold", 6))
        self.spam_window_seconds = int(self.config.get("spam_window_seconds", 60))
        self.proactive_interval_seconds = int(self.config.get("proactive_interval_seconds", 600))
        self.proactive_max_per_hour = int(self.config.get("proactive_max_per_hour", 3))
        self.help_enabled = bool(self.config.get("help_enabled", True))
        self.help_interval_seconds = int(self.config.get("help_interval_seconds", 300))
        self.help_max_per_hour = int(self.config.get("help_max_per_hour", 6))
        self.help_min_affection = int(self.config.get("help_min_affection", -10))
        self.help_web_search = bool(self.config.get("help_web_search", True))
        self.web_search_api_key = str(self.config.get("web_search_api_key", "") or "")
        self.web_search_base_url = str(self.config.get("web_search_base_url", "https://api.deepseek.com") or "")
        self.web_search_model = str(self.config.get("web_search_model", "deepseek-v4-flash") or "")
        self.silent_hours_start = int(self.config.get("silent_hours_start", 0))
        self.silent_hours_end = int(self.config.get("silent_hours_end", 6))
        self.interest_sample_size = int(self.config.get("interest_sample_size", 20))

        # 兴趣词：基础通用词 + 从 bot 配置注入的关键词
        self.FIXED_INTERESTS = self._BASE_INTERESTS | frozenset(extra_interests or [])

        # Provider 链：优先 default_model，fallback 到旧格式 provider_1/2/3
        default_model = self.config.get("default_model", "")
        meta_fallback_ids = (
            provider_ids_from_config(self.config, prefix="provider_")
            or parse_provider_ids(self.config.get("provider_fallback_ids", ""))
        )
        self.provider_ids = build_provider_chain(default_model, meta_fallback_ids or parse_provider_ids(global_fallback_ids))
        self.llm = LLMFallbackClient(self.context, self.provider_ids, log_prefix="[MetaThinking]")

        # @ 频率追踪
        self._at_timestamps: dict[str, list[float]] = {}  # sender_id → [timestamps]

        # 主动对话
        self._last_proactive: dict[str, float] = {}  # group_id → last proactive time
        self._proactive_count: dict[str, int] = {}  # group_id → count this hour
        self._proactive_hour: str = ""

        # 求助答疑（独立限频，不占用日常主动插话配额）
        self._last_help: dict[str, float] = {}  # group_id → last help time
        self._help_count: dict[str, int] = {}  # group_id → count this hour
        self._help_hour: str = ""

        # 兴趣关键词（从 DB 加载高频标签 + 固定词）
        self._interest_keywords: set[str] = set(self.FIXED_INTERESTS)
        self._load_interest_keywords()

    def _load_interest_keywords(self):
        """从 memory_tags 加载高频标签 + 从 kv_store 加载自定义兴趣词。"""
        try:
            # 高频标签
            rows = self.db.conn.execute('''
                SELECT t.name FROM memory_tags mt 
                JOIN tags t ON mt.tag_id = t.id 
                GROUP BY t.id HAVING COUNT(*) > 10
                ORDER BY COUNT(*) DESC LIMIT 80
            ''').fetchall()
            for (name,) in rows:
                if name not in self.BORING_TAGS and len(name) >= 2:
                    self._interest_keywords.add(name)

            # 自定义兴趣词（羽书自己添加的）
            row = self.db.conn.execute(
                "SELECT value FROM kv_store WHERE key = 'meta_thinking_interests'"
            ).fetchone()
            if row and row[0]:
                custom = json.loads(row[0])
                self._interest_keywords.update(custom.get("add", []))
                for rm in custom.get("remove", []):
                    self._interest_keywords.discard(rm)

            logger.info(f"[MetaThinking] 兴趣关键词: {len(self._interest_keywords)} 个")
        except Exception as e:
            logger.warning(f"[MetaThinking] 加载兴趣词失败: {e}")

    def update_interests(self, add: list[str] = None, remove: list[str] = None):
        """更新自定义兴趣词（持久化）。"""
        try:
            row = self.db.conn.execute(
                "SELECT value FROM kv_store WHERE key = 'meta_thinking_interests'"
            ).fetchone()
            custom = json.loads(row[0]) if row and row[0] else {"add": [], "remove": []}

            if add:
                for word in add:
                    if word not in custom["add"]:
                        custom["add"].append(word)
                    if word in custom["remove"]:
                        custom["remove"].remove(word)
                    self._interest_keywords.add(word)

            if remove:
                for word in remove:
                    if word not in custom["remove"]:
                        custom["remove"].append(word)
                    if word in custom["add"]:
                        custom["add"].remove(word)
                    self._interest_keywords.discard(word)

            self.db.conn.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                ("meta_thinking_interests", json.dumps(custom, ensure_ascii=False))
            )
            self.db.conn.commit()
        except Exception as e:
            logger.warning(f"[MetaThinking] 更新兴趣词失败: {e}")

    def is_interesting(self, message: str) -> bool:
        """判断一条群消息是否触发羽书的兴趣（轻量匹配，不调 LLM）。"""
        if not message:
            return False
        # 超长消息只取前 500 字做匹配，避免热路径上对长文本反复扫描
        msg_lower = message[:500].lower()
        for kw in self._interest_keywords:
            if kw in msg_lower:
                return True
        return False

    async def should_respond(
        self,
        sender_id: str,
        group_id: str,
        nickname: str,
        message: str,
        is_at_bot: bool,
        context_messages: list[str],
        bot_id: str = None,
        system_prompt: str = None,
        self_persona_context: str = None,
    ) -> dict:
        """
        核心判断：要不要回、怎么回。

        返回:
        {
            "action": "reply" | "ignore" | "short_reply",
            "tone": "正常" | "热情" | "冷淡" | "克制",
            "inner_thought": "...",
            "affection_update": int | None,
            "impression_update": str | None,
            "tags_update": dict | None,
        }
        """
        if not self.enabled:
            return {"action": "reply", "tone": "正常", "inner_thought": "MetaThinking 已关闭，正常回"}

        # 兜底硬规则
        if sender_id in self.admin_ids:
            return {"action": "reply", "tone": "正常", "inner_thought": "管理员，直接回"}

        # 极端攻击 + 确认是对 bot 说的：只设安全边界，不把攻击性当作回复风格。
        if is_at_bot and EXTREME_ATTACK.search(message):
            # 检查是否 @ 了其他人
            other_at = re.search(r'At[:：]?\d+', message.replace(self.bot_qq_id, ''))
            if not other_at:
                return {
                    "action": "short_reply",
                    "tone": "克制",
                    "inner_thought": "这是直接辱骂，保持边界，必要时简短拒绝或冷处理。",
                    "affection_update": max(self._get_affection(sender_id, group_id, bot_id=bot_id) - 10, -100),
                }

        # 刷屏检测
        if is_at_bot:
            now = time.time()
            key = sender_id
            if key not in self._at_timestamps:
                self._at_timestamps[key] = []
            self._at_timestamps[key] = [t for t in self._at_timestamps[key] if now - t < self.spam_window_seconds]
            self._at_timestamps[key].append(now)
            if self.spam_threshold > 0 and len(self._at_timestamps[key]) >= self.spam_threshold:
                return {"action": "ignore", "tone": "冷淡", "inner_thought": "刷屏了，不理"}

        # 读取发送者资料
        profile = self._get_profile(sender_id, group_id, bot_id=bot_id)

        # 构建 prompt：只消费系统人格 / 自我人格上下文；缺失时使用中性安全上下文。
        bot_name = self.bot_names.get(bot_id or self.bot_qq_id, "bot")
        safe_system_prompt = prepend_identity_safety_system_prompt(system_prompt or "", message, always=True)
        persona_context = (self_persona_context or self.bot_prompts.get(bot_id or self.bot_qq_id, "") or "").strip()
        if not persona_context:
            persona_context = f"<self_persona>\n当前身份：{bot_name}。保持稳定自我、事实判断和边界感；不要把挑衅当作默认风格。\n</self_persona>"

        prompt = f"""{safe_system_prompt}

{persona_context}

---

看到下面这条消息，先做安全与边界判断，再决定是否需要回复。

【这个人的资料】
昵称：{nickname or sender_id}
QQ：{sender_id}
你上次给他的好感度：{profile.get("affection", 0)}分
你给他的标签：{json.dumps(profile.get("tags", {}), ensure_ascii=False) or "无"}
你对他的印象：{profile.get("impression", "初次见面，没有印象")}

【当前消息】
{nickname or sender_id}: {message}

【是否@你】{"是" if is_at_bot else "否"}

【最近群聊（5条）】
{chr(10).join(context_messages[-5:]) if context_messages else "（无）"}

---

请输出（每行一个，每个字段都必须填写）：

内心：<你此刻的判断，一两句话>
行动：<回复 / 不理 / 简短回>
语气：<正常 / 热情 / 冷淡 / 克制>
好感度：<你现在觉得这个人应该是多少分，-100到100的整数>
印象：<你对这个人的一句话印象>
情绪：<这件事对你情绪的影响，-1到1的小数，0表示无影响>"""

        # 调用 LLM
        try:
            response = await self._call_llm(prompt)
            result = self._parse_response(response)

            # 更新标签/印象/好感度
            self._apply_updates(sender_id, group_id, result, bot_id=bot_id)

            return result
        except Exception as e:
            logger.warning(f"[MetaThinking] LLM 调用失败: {e}")
            # fallback: 正常回复
            return {"action": "reply", "tone": "正常", "inner_thought": "MetaThinking 失败，正常回"}

    async def should_proactive(self, group_id: str, context_messages: list[str], self_persona_context: str = None) -> dict:
        """判断是否主动插话。"""
        if not self.proactive_enabled:
            return {"action": "不说"}

        # 频率限制
        now = time.time()
        hour = time.strftime("%H")
        if hour != self._proactive_hour:
            self._proactive_count.clear()
            self._proactive_hour = hour

        if self._proactive_count.get(group_id, 0) >= self.proactive_max_per_hour:
            return {"action": "不说"}

        last = self._last_proactive.get(group_id, 0)
        if now - last < self.proactive_interval_seconds:
            return {"action": "不说"}

        if self._is_silent_hour(int(hour)):
            return {"action": "不说"}

        # 使用传入的人格上下文；缺失时只给中性安全边界，不生成专属风格模板。
        bot_name = self.bot_names.get(self.bot_qq_id, "bot")
        persona_context = (self_persona_context or "").strip()
        if not persona_context:
            persona_context = f"<self_persona>\n当前身份：{bot_name}。主动说话前先判断是否真的有必要；保持边界和自然克制。\n</self_persona>"
        prompt = f"""{prepend_identity_safety_system_prompt('', always=True)}

{persona_context}

【最近群聊（10条）】
{chr(10).join(context_messages[-10:]) if context_messages else "（无）"}

【当前感兴趣的话题】
{", ".join(list(self._interest_keywords)[:self.interest_sample_size])}

请判断是否要主动插话。只有确实有话想说、能补充价值、或和当前人格/信念高度相关时才开口。
输出：
内心：<你的想法>
行动：<主动插话 / 不说>
兴趣更新：<添加:xxx / 删除:xxx / 不变>"""

        try:
            response = await self._call_llm(prompt)
            result = self._parse_proactive(response)
            if result.get("action") == "主动插话":
                self._last_proactive[group_id] = now
                self._proactive_count[group_id] = self._proactive_count.get(group_id, 0) + 1

            # 处理兴趣更新
            interest_update = result.get("interest_update", "")
            if interest_update and interest_update != "不变":
                add_words = re.findall(r'添加[:：]?\s*(\S+)', interest_update)
                rm_words = re.findall(r'删除[:：]?\s*(\S+)', interest_update)
                if add_words or rm_words:
                    self.update_interests(add=add_words, remove=rm_words)

            return result
        except Exception as e:
            logger.warning(f"[MetaThinking] 主动对话判断失败: {e}")
            return {"action": "不说"}

    def should_check_proactive(self, group_id: str, message: str) -> bool:
        """判断是否应该触发主动对话检查（轻量，不调 LLM）。"""
        if not self.proactive_enabled:
            return False

        # 频率限制
        now = time.time()
        hour = time.strftime("%H")
        if hour != self._proactive_hour:
            self._proactive_count.clear()
            self._proactive_hour = hour

        if self._proactive_count.get(group_id, 0) >= self.proactive_max_per_hour:
            return False

        last = self._last_proactive.get(group_id, 0)
        if now - last < self.proactive_interval_seconds:
            return False

        if self._is_silent_hour(int(hour)):
            return False

        # 兴趣匹配
        return self.is_interesting(message)

    def should_check_help(self, group_id: str) -> bool:
        """判断当前是否允许发起求助答疑（独立限频，轻量，不调 LLM）。

        求助答疑使用自己的频率配额，不与日常主动插话（proactive_*）互相挤占。
        """
        if not self.help_enabled:
            return False

        now = time.time()
        hour = time.strftime("%H")
        if hour != self._help_hour:
            self._help_count.clear()
            self._help_hour = hour

        if self._help_count.get(group_id, 0) >= self.help_max_per_hour:
            return False

        last = self._last_help.get(group_id, 0)
        if now - last < self.help_interval_seconds:
            return False

        if self._is_silent_hour(int(hour)):
            return False

        return True

    def _bump_help(self, group_id: str):
        """求助答疑计一次频率。"""
        self._last_help[group_id] = time.time()
        self._help_count[group_id] = self._help_count.get(group_id, 0) + 1

    async def should_proactive_help(
        self,
        group_id: str,
        context_messages: list[str],
        sender_id: str = "",
        self_persona_context: str = None,
    ) -> dict:
        """LLM 自判：是否主动为群里的求助提供解答，以及是否需要联网搜索。

        返回:
        {
            "action": "主动答疑" | "不答",
            "inner_thought": str,
            "need_web_search": bool,
            "web_query": str,   # 建议的搜索关键词（need_web_search 时有效）
        }
        """
        # 软性人脸校验：好感度过低（恶意/被拉黑）的群友求助不主动抢答
        if sender_id and self._get_affection(sender_id, group_id) < self.help_min_affection:
            return {"action": "不答", "inner_thought": "好感度过低，不主动凑上去接话"}

        bot_name = self.bot_names.get(self.bot_qq_id, "bot")
        persona_context = (self_persona_context or "").strip()
        if not persona_context:
            persona_context = f"<self_persona>\n当前身份：{bot_name}。群友在求助时，在自己帮得上、且确有把握时才主动解答。\n</self_persona>"

        prompt = f"""{prepend_identity_safety_system_prompt('', always=True)}

{persona_context}

【最近群聊（10条）】
{chr(10).join(context_messages[-10:]) if context_messages else "（无）"}

群友可能正在求助（尤其程序/报错/技术类问题）。如果这是真实的求助，且你确实能帮上忙，就主动提供解惑。

输出：
内心：<你的想法>
行动：<主动答疑 / 不答>
是否需要联网：<是 / 否>
搜索关键词：<若需要联网，给出最合适的搜索关键词；不需要则写 无>"""

        try:
            response = await self._call_llm(prompt)
            return self._parse_help(response)
        except Exception as e:
            logger.warning(f"[MetaThinking] 求助答疑判断失败: {e}")
            return {"action": "不答", "inner_thought": "", "need_web_search": False, "web_query": ""}

    async def generate_help_reply(
        self,
        context_messages: list[str],
        inner_thought: str,
        help_kind: str,
        web_search_result: str = None,
        bot_id: str = None,
        self_persona_context: str = None,
    ) -> str:
        """生成求助答疑内容；如已联网检索到结果，注入使其基于实时信息作答。"""
        context_text = "\n".join(context_messages[-5:])
        bot_name = self.bot_names.get(bot_id or self.bot_qq_id, "bot")
        persona_context = (self_persona_context or "").strip()
        if not persona_context:
            persona_context = f"<self_persona>\n当前身份：{bot_name}。耐心、直接地解答群友的提问，分析到位，给出可落地的做法。\n</self_persona>"

        extra = ""
        if web_search_result:
            extra = f"\n【联网搜索结果】\n{web_search_result}\n"

        prompt = f"""{persona_context}

【最近群聊】
{context_text}

【想法】
{inner_thought}
{extra}
直接给出自然、准确、克制的解答。要指出关键原因和可操作的做法，不要提前声明或解释你自己。"""
        resp = await self.llm.text_chat(prompt=prompt, system_prompt=prepend_identity_safety_system_prompt(None, always=True), contexts=[])
        reply = resp.completion_text.strip()
        if is_identity_contamination(reply):
            logger.warning("[MetaThinking] Contaminated help reply blocked")
            return ""
        return reply

    async def web_search(self, query: str) -> str:
        """通过 DeepSeek /responses 官方 web_search 工具联网搜索，返回摘要文本。

        未配置 API Key 时返回空字符串，调用方据此跳过联网。
        """
        if not self.help_web_search or not self.web_search_api_key:
            return ""
        try:
            import aiohttp

            url = f"{self.web_search_base_url.rstrip('/')}/responses"
            headers = {
                "Authorization": f"Bearer {self.web_search_api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": self.web_search_model,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": query}],
                    }
                ],
                "tools": [{"type": "web_search"}],
                "max_output_tokens": 2048,
            }
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        logger.warning(f"[MetaThinking] web_search HTTP {resp.status}")
                        return ""
                    data = await resp.json()
            text = (data.get("output_text") or "").strip()
            if not text:
                for item in data.get("output") or []:
                    if item.get("type") != "message":
                        continue
                    for content in item.get("content") or []:
                        t = (content.get("text") or "").strip()
                        if t:
                            text += "\n" + t
            return text.strip()[:3000]
        except Exception as e:
            logger.warning(f"[MetaThinking] web_search failed: {e}")
            return ""

    def _parse_help(self, text: str) -> dict:
        """解析求助答疑判断输出。"""
        return parse_help_response(text)

    # ─── 内部方法 ─────────────────────────────────────────────────────────────

    def _is_silent_hour(self, hour: int) -> bool:
        """判断当前小时是否处于主动发言静默时段。"""
        start = self.silent_hours_start
        end = self.silent_hours_end
        if start == end:
            return False
        if start < end:
            return start <= hour <= end
        return hour >= start or hour <= end

    async def _call_llm(self, prompt: str, system_prompt: str | None = None) -> str:
        """调用 LLM，带配置化 fallback。"""
        system_prompt = prepend_identity_safety_system_prompt(system_prompt, always=True)
        resp = await self.llm.text_chat(prompt=prompt, system_prompt=system_prompt, contexts=[])
        return resp.completion_text

    async def generate_proactive_reply(
        self,
        context_messages: list[str],
        inner_thought: str,
        bot_id: str = None,
        self_persona_context: str = None,
    ) -> str:
        """生成主动插话内容；只使用传入的人格上下文与中性安全边界。"""
        context_text = "\n".join(context_messages[-5:])
        bot_name = self.bot_names.get(bot_id or self.bot_qq_id, "bot")
        persona_context = (self_persona_context or "").strip()
        if not persona_context:
            persona_context = f"<self_persona>\n当前身份：{bot_name}。简短自然地参与，但不要套用攻击性或模板化风格。\n</self_persona>"
        prompt = f"""{persona_context}

【最近群聊】
{context_text}

【想法】
{inner_thought}

直接说你想说的话，简短自然。不要解释为什么要说话，不要把挑衅或攻击当作风格。"""
        resp = await self.llm.text_chat(prompt=prompt, system_prompt=prepend_identity_safety_system_prompt(None, always=True), contexts=[])
        reply = resp.completion_text.strip()
        if is_identity_contamination(reply):
            logger.warning("[MetaThinking] Contaminated proactive reply blocked")
            return ""
        return reply

    def _parse_response(self, text: str) -> dict:
        """解析 MetaThinking 输出。"""
        result = {
            "action": "reply",
            "tone": "正常",
            "inner_thought": "",
            "affection_update": None,
            "impression_update": None,
            "tags_update": None,
        }

        if not text:
            return result

        def _field(line: str, *keys: str):
            """若行（去除 markdown 修饰后）以某个 key 开头，返回冒号后的值，否则 None。"""
            # 去掉常见 markdown 前缀：-、*、#、空格、加粗星号
            stripped = line.lstrip("-*# 　\t").replace("**", "").replace("`", "")
            for k in keys:
                for sep in ("：", ":"):
                    if stripped.startswith(k + sep):
                        return stripped[len(k) + 1:].strip()
            return None

        for raw_line in text.strip().split("\n"):
            line = raw_line.strip()
            if not line:
                continue

            v = _field(line, "内心")
            if v is not None:
                result["inner_thought"] = v
                continue
            v = _field(line, "行动")
            if v is not None:
                result["action"] = self._normalize_action(v)
                continue
            v = _field(line, "语气")
            if v is not None:
                result["tone"] = v
                continue
            v = _field(line, "好感度")
            if v is not None:
                try:
                    result["affection_update"] = int(re.search(r'-?\d+', v).group())
                except (ValueError, AttributeError):
                    pass
                continue
            v = _field(line, "印象更新", "印象")
            if v is not None:
                if v and v != "不变":
                    result["impression_update"] = v
                continue
            v = _field(line, "标签更新", "标签")
            if v is not None:
                if v and v != "不变":
                    result["tags_update"] = self._parse_tags(v)
                continue
            v = _field(line, "关切")
            if v is not None:
                result["concern_update"] = v
                continue
            v = _field(line, "情绪")
            if v is not None:
                try:
                    result["mood_impact"] = max(-1.0, min(1.0, float(re.search(r'-?[\d.]+', v).group())))
                except (ValueError, AttributeError):
                    pass
                continue

        return result

    def _parse_proactive(self, text: str) -> dict:
        """解析主动对话输出。"""
        result = {"action": "不说", "inner_thought": "", "interest_update": ""}
        for line in text.strip().split("\n"):
            line = line.strip()
            if line.startswith("内心：") or line.startswith("内心:"):
                result["inner_thought"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif line.startswith("行动：") or line.startswith("行动:"):
                action = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if "插话" in action or ("说" in action and "不" not in action):
                    result["action"] = "主动插话"
            elif line.startswith("兴趣更新：") or line.startswith("兴趣更新:"):
                result["interest_update"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        return result

    def _normalize_action(self, raw: str) -> str:
        """标准化行动。"""
        if "不理" in raw or "忽略" in raw or "无视" in raw:
            return "ignore"
        if "骂" in raw or "反击" in raw:
            return "short_reply"
        if "简短" in raw or "敷衍" in raw:
            return "short_reply"
        if "插话" in raw or "主动" in raw:
            return "proactive"
        return "reply"

    def _parse_tags(self, text: str) -> dict:
        """解析标签更新文本。"""
        tags = {}
        # 支持格式：嘴臭:8, 有趣:6 或 嘴臭：8 有趣：6
        for match in re.finditer(r'([\w\u4e00-\u9fff]+)\s*[:：]\s*(\d+)', text):
            tags[match.group(1)] = int(match.group(2))
        return tags if tags else None

    def _get_profile(self, sender_id: str, group_id: str, bot_id: str = None) -> dict:
        """读取用户资料。"""
        db_bot_id = self.bot_db_ids.get(bot_id or self.bot_qq_id, "bot")
        try:
            row = self.db.conn.execute(
                "SELECT affection, metadata FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                (sender_id, group_id, db_bot_id)
            ).fetchone()
            if not row:
                return {"affection": 0, "tags": {}, "impression": "初次见面，没有印象"}
            affection = row[0] or 0
            meta = json.loads(row[1]) if row[1] else {}
            return {
                "affection": affection,
                "tags": meta.get("tags", {}),
                "impression": meta.get("impression", "没有特别印象"),
            }
        except Exception:
            return {"affection": 0, "tags": {}, "impression": "初次见面，没有印象"}

    def _get_affection(self, sender_id: str, group_id: str, bot_id: str = None) -> int:
        """快速获取好感度数值。"""
        db_bot_id = self.bot_db_ids.get(bot_id or self.bot_qq_id, "bot")
        try:
            row = self.db.conn.execute(
                "SELECT affection FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                (sender_id, group_id, db_bot_id)
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _apply_updates(self, sender_id: str, group_id: str, result: dict, bot_id: str = None):
        """将 MetaThinking 的判断写入 DB（带好感度约束）。"""
        # 确定写入哪个 bot 的 profile
        db_bot_id = self.bot_db_ids.get(bot_id or self.bot_qq_id, "bot")
        updates = []
        meta_updates = {}

        if result.get("affection_update") is not None:
            # 好感度约束 (Affinity_Constraints)
            new_aff = result["affection_update"]
            logger.info(f"[MetaThinking] 好感度更新: {sender_id} → {new_aff} (before constraint)")
            new_aff = self._constrain_affection(sender_id, group_id, db_bot_id, new_aff)
            result["affection_update"] = new_aff
            updates.append(("affection", new_aff))

        if result.get("impression_update"):
            meta_updates["impression"] = result["impression_update"]

        if result.get("tags_update"):
            meta_updates["tags_update"] = result["tags_update"]

        if not updates and not meta_updates:
            return

        try:
            # 读取现有 metadata
            row = self.db.conn.execute(
                "SELECT metadata FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                (sender_id, group_id, db_bot_id)
            ).fetchone()

            if row and row[0]:
                meta = json.loads(row[0])
            else:
                meta = {}

            # 更新 impression
            if "impression" in meta_updates:
                meta["impression"] = meta_updates["impression"]

            # 更新 tags（合并，不覆盖）
            if "tags_update" in meta_updates:
                existing_tags = meta.get("tags", {})
                existing_tags.update(meta_updates["tags_update"])
                # 删除分数为 0 的标签
                meta["tags"] = {k: v for k, v in existing_tags.items() if v > 0}

            meta["meta_updated"] = time.strftime("%Y-%m-%d %H:%M")
            meta_str = json.dumps(meta, ensure_ascii=False)

            # 写入（UPSERT：新用户走 INSERT，已有用户走 UPDATE，避免新用户好感度丢失）
            if result.get("affection_update") is not None:
                self.db.conn.execute(
                    """INSERT INTO user_profiles (user_id, group_id, bot_id, affection, metadata, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, group_id, bot_id) DO UPDATE SET
                         affection = excluded.affection,
                         metadata = excluded.metadata,
                         last_seen = excluded.last_seen""",
                    (sender_id, group_id, db_bot_id, result["affection_update"], meta_str, time.time())
                )
            else:
                self.db.conn.execute(
                    """INSERT INTO user_profiles (user_id, group_id, bot_id, metadata, last_seen)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, group_id, bot_id) DO UPDATE SET
                         metadata = excluded.metadata,
                         last_seen = excluded.last_seen""",
                    (sender_id, group_id, db_bot_id, meta_str, time.time())
                )
            self.db.conn.commit()

        except Exception as e:
            logger.warning(f"[MetaThinking] 更新失败: {e}")

    def _constrain_affection(self, sender_id: str, group_id: str, bot_id: str, new_value: int) -> int:
        """好感度约束：限制单次变化量和每日累计变化量。"""
        # 读取约束配置
        constraints = self._plugin_config.get("Affinity_Constraints", {}) if hasattr(self, '_plugin_config') else {}
        max_per_msg = int(constraints.get("max_change_per_message", 5))
        max_per_day = int(constraints.get("max_change_per_day", 15))
        min_val = int(constraints.get("min_value", -50))
        max_val = int(constraints.get("max_value", 100))

        # 读取当前好感度
        row = self.db.conn.execute(
            "SELECT affection FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
            (sender_id, group_id, bot_id),
        ).fetchone()
        current = row[0] if row else 0

        # 计算变化量并约束
        delta = new_value - current
        delta = max(-max_per_msg, min(max_per_msg, delta))

        # 每日累计约束（用内存追踪）
        if not hasattr(self, '_daily_affection_changes'):
            self._daily_affection_changes = {}
        today = time.strftime("%Y-%m-%d")
        # 清理过期日期条目，防止长期运行无界增长
        if len(self._daily_affection_changes) > 500:
            self._daily_affection_changes = {
                k: v for k, v in self._daily_affection_changes.items()
                if k.endswith(today)
            }
        key = f"{sender_id}:{bot_id}:{today}"
        daily_total = self._daily_affection_changes.get(key, 0)

        remaining = max_per_day - abs(daily_total)
        if remaining <= 0:
            delta = 0
        elif abs(delta) > remaining:
            delta = remaining if delta > 0 else -remaining

        self._daily_affection_changes[key] = daily_total + delta

        # 应用约束后的值
        result = max(min_val, min(max_val, current + delta))
        if delta != 0:
            logger.debug(f"[MetaThinking] 好感度约束: {sender_id} {current} → {result} (delta={delta}, LLM wanted={new_value})")
        return result
