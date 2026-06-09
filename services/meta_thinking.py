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


# 兜底硬规则
EXTREME_ATTACK = re.compile(r'(你[妈马]|nmsl|死[全妈]|全家|操你|fuck\s*you|滚去死|杀了你|弄死你)')


META_THINKING_PROMPT = """你是{bot_name}。你是一个有感情的人。你有喜好、有脾气、有记忆。

看到下面这条消息，在心里想一下，然后决定怎么做。

【这个人的资料】
昵称：{nickname}
QQ：{qq}
你上次给他的好感度：{affection}分（这是你自己之前的感受，不是系统算的。你现在可以根据此刻的感觉重新打分）
你给他的标签：{tags}
你对他的印象：{impression}

【当前消息】
{sender_name}: {message}

【是否@你】{is_at}

【最近群聊（5条）】
{context}

---

你面前有这个人的历史数据，这些是你之前的感受记录。
你可以参考它们，也可以推翻它们——如果你现在的感觉和之前不一样。
你不是在执行规则，你是在做一个真实的判断：此刻，你对这个人什么感觉，你想怎么做。

请输出（每行一个，每个字段都必须填写，不能省略）：

内心：<你此刻的真实想法，一两句话>
行动：<回复 / 不理 / 怼回去 / 简短回 / 主动插话>
语气：<正常 / 热情 / 冷淡 / 讽刺 / 愤怒>
好感度：<你现在觉得这个人应该是多少分，-100到100的整数，必须给出数字>
印象：<你对这个人的一句话印象，每次都写>
标签：<给这个人的标签，格式 name:score name:score，至少写一个>"""


PROACTIVE_PROMPT = """你是{bot_name}。你在群里旁观，没有被@。

【最近群聊（10条）】
{context}

【你当前感兴趣的话题】
{interests_sample}

---

你是一个有感情的人。看看群里在聊什么，想想要不要主动说点什么。
不要为了说话而说话。只有真的有话想说、或者觉得有趣想参与时才开口。

请输出：

内心：<你的想法>
行动：<主动插话 / 不说>
兴趣更新：<如果发现新的感兴趣话题写"添加:xxx"，觉得某话题无聊了写"删除:xxx"，没变化写"不变">"""


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
        # bot_id → db_id 映射（用于数据库写入时的标识）
        # 默认用 bot_name 小写作为 db_id
        self.bot_db_ids = {bid: name.lower() for bid, name in self.bot_names.items()}
        self.admin_ids = set(admin_ids or [])
        self.config = config or {}
        self.enabled = bool(self.config.get("enabled", True))
        self.proactive_enabled = bool(self.config.get("proactive_enabled", True))
        self.spam_threshold = int(self.config.get("spam_threshold", 6))
        self.spam_window_seconds = int(self.config.get("spam_window_seconds", 60))
        self.proactive_interval_seconds = int(self.config.get("proactive_interval_seconds", 600))
        self.proactive_max_per_hour = int(self.config.get("proactive_max_per_hour", 3))
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
    ) -> dict:
        """
        核心判断：要不要回、怎么回。

        返回:
        {
            "action": "reply" | "ignore" | "attack_back" | "short_reply",
            "tone": "正常" | "热情" | "冷淡" | "讽刺" | "愤怒",
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

        # 极端攻击 + 确认是对 bot 说的
        if is_at_bot and EXTREME_ATTACK.search(message):
            # 检查是否 @ 了其他人
            other_at = re.search(r'At[:：]?\d+', message.replace(self.bot_qq_id, ''))
            if not other_at:
                return {
                    "action": "attack_back",
                    "tone": "愤怒",
                    "inner_thought": "这人在骂我，怼回去",
                    "affection_update": max(self._get_affection(sender_id, group_id) - 10, -100),
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
        profile = self._get_profile(sender_id, group_id)

        # 构建 prompt（按 bot_id 选对应模板，没有则用默认）
        prompt_template = self.bot_prompts.get(bot_id or self.bot_qq_id, META_THINKING_PROMPT)
        bot_name = self.bot_names.get(bot_id or self.bot_qq_id, "bot")
        prompt = prompt_template.format(
            bot_name=bot_name,
            nickname=nickname or sender_id,
            qq=sender_id,
            affection=profile.get("affection", 0),
            tags=json.dumps(profile.get("tags", {}), ensure_ascii=False) or "无",
            impression=profile.get("impression", "初次见面，没有印象"),
            sender_name=nickname or sender_id,
            message=message,
            is_at="是" if is_at_bot else "否",
            context="\n".join(context_messages[-5:]) if context_messages else "（无）",
        )

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

    async def should_proactive(self, group_id: str, context_messages: list[str]) -> dict:
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

        # 使用主 bot 名称
        bot_name = self.bot_names.get(self.bot_qq_id, "bot")
        prompt = PROACTIVE_PROMPT.format(
            bot_name=bot_name,
            context="\n".join(context_messages[-10:]) if context_messages else "（无）",
            interests_sample=", ".join(list(self._interest_keywords)[:self.interest_sample_size]),
        )

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

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM，带配置化 fallback。"""
        resp = await self.llm.text_chat(prompt=prompt, contexts=[])
        return resp.completion_text

    async def generate_proactive_reply(self, context_messages: list[str], inner_thought: str, bot_id: str = None) -> str:
        """生成主动插话内容，使用同一套 MetaThinking fallback。"""
        context_text = "\n".join(context_messages[-5:])
        bot_name = self.bot_names.get(bot_id or self.bot_qq_id, "bot")
        prompt = f"你是{bot_name}，刚才群里在聊：\n{context_text}\n\n你想插一嘴。你的想法：{inner_thought}\n\n直接说你想说的话，简短自然，像群友一样。不要解释为什么要说话。"
        resp = await self.llm.text_chat(prompt=prompt, contexts=[])
        return resp.completion_text.strip()

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
        if "怼" in raw or "骂" in raw or "反击" in raw:
            return "attack_back"
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

    def _get_profile(self, sender_id: str, group_id: str) -> dict:
        """读取用户资料。"""
        try:
            row = self.db.conn.execute(
                "SELECT affection, metadata FROM user_profiles WHERE user_id = ? AND group_id = ?",
                (sender_id, group_id)
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

    def _get_affection(self, sender_id: str, group_id: str) -> int:
        """快速获取好感度数值。"""
        try:
            row = self.db.conn.execute(
                "SELECT affection FROM user_profiles WHERE user_id = ? AND group_id = ?",
                (sender_id, group_id)
            ).fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def _apply_updates(self, sender_id: str, group_id: str, result: dict, bot_id: str = None):
        """将 MetaThinking 的判断写入 DB。"""
        # 确定写入哪个 bot 的 profile
        db_bot_id = self.bot_db_ids.get(bot_id or self.bot_qq_id, "bot")
        updates = []
        meta_updates = {}

        if result.get("affection_update") is not None:
            aff = max(-100, min(100, result["affection_update"]))
            updates.append(("affection", aff))

        if result.get("impression_update"):
            meta_updates["impression"] = result["impression_update"]

        if result.get("tags_update"):
            meta_updates["tags_update"] = result["tags_update"]

        if not updates and not meta_updates:
            return

        try:
            # 读取现有 metadata
            row = self.db.conn.execute(
                "SELECT metadata FROM user_profiles WHERE user_id = ? AND group_id = ?",
                (sender_id, group_id)
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

            # 写入
            if result.get("affection_update") is not None:
                self.db.conn.execute(
                    "UPDATE user_profiles SET affection = ?, metadata = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                    (result["affection_update"], meta_str, sender_id, group_id, db_bot_id)
                )
            else:
                self.db.conn.execute(
                    "UPDATE user_profiles SET metadata = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                    (meta_str, sender_id, group_id, db_bot_id)
                )
            self.db.conn.commit()

        except Exception as e:
            logger.warning(f"[MetaThinking] 更新失败: {e}")
