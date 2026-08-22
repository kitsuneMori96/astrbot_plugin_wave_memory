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


# ─── 其他 bot 发言识别（纯函数）：名单优先 + 启发式兜底，避免 bot 互聊循环 ───
def detect_other_bot_message(
    sender_id: str,
    message: str,
    last_send_ts: float = 0.0,
    now: float = None,
    other_bot_ids: set = None,
    heuristic_enabled: bool = True,
    quick_seconds: int = 10,
    min_length: int = 80,
) -> bool:
    """判断这条消息是否疑似其他 bot 的发言。

    名单命中：无条件视为其他 bot（含被 @ 也不回复）。
    启发式：bot 刚发言后的极短窗口内（< quick_seconds 秒）到达的超长文本
    （>= min_length 字），视为疑似 bot 秒回。
    """
    if sender_id and other_bot_ids and sender_id in other_bot_ids:
        return True
    if not heuristic_enabled:
        return False
    if not last_send_ts:
        return False
    if now is None:
        now = time.time()
    if now - last_send_ts >= quick_seconds:
        return False
    return len(message or "") >= min_length


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
