"""黑话服务 — 挖掘调度 + DB 读写 + 跨群合并 (US-4.1~4.5)"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from .statistical_filter import JargonStatisticalFilter
from .inference import JargonInferenceEngine, JargonInjector
from .holyman_reference import HolymanReference


_TECHNICAL_NOISE_WORDS = {
    "id", "ids", "json", "api", "url", "uri", "http", "https", "get", "post", "put", "patch", "delete",
    "from", "has", "object", "objects", "array", "list", "dict", "map", "set", "type", "types",
    "value", "values", "data", "item", "items", "key", "keys", "param", "params", "args", "kwargs",
    "none", "null", "true", "false", "bool", "str", "int", "float", "class", "method", "function",
    "return", "import", "async", "await", "self", "this", "const", "let", "var",
}

_ORDINARY_WORDS = {
    "吃饭", "睡觉", "上班", "下班", "回家", "出门", "上课", "工作", "学习", "考试",
    "好的", "可以", "谢谢", "没事", "不用", "不是", "没有", "手机", "电脑", "学校",
    "今天", "昨天", "明天", "现在", "刚才", "马上", "哈哈", "哈哈哈", "嗯嗯", "呵呵",
    "朋友", "同学", "老师", "家人", "爸爸", "妈妈", "真的", "确实", "其实", "当然",
    "知道", "不知道", "怎么", "什么", "为什么", "这个", "那个",
}


class JargonService:
    """黑话系统主服务。"""

    def __init__(self, db: Any, llm_client: Any = None, enabled: bool = True, config: dict = None):
        self._db = db
        self._enabled = enabled
        self._config = config or {}
        self._llm = llm_client

        # 从配置读取参数（改动4：全部参数配置化）
        self._min_frequency = int(self._config.get("min_frequency", 5))
        self._global_threshold = int(self._config.get("global_threshold", 5))
        self._min_messages = int(self._config.get("min_messages", 10))
        self._mine_cooldown = int(self._config.get("mine_cooldown", 300))
        self._top_k = int(self._config.get("top_k", 20))
        self._max_context = int(self._config.get("max_context", 15))
        self._context_keep = int(self._config.get("context_keep", 10))
        self._window_days = int(self._config.get("window_days", 7))
        self._jieba_threshold = int(self._config.get("jieba_threshold", 500))
        _llm_validate_cfg = self._config.get("llm_validate", True)
        self._llm_validate = True if _llm_validate_cfg is None else bool(_llm_validate_cfg)
        self._confidence_threshold = float(self._config.get("confidence_threshold", 0.5))
        self._holyman = HolymanReference(self._config.get("holyman_path") or None) if self._config.get("holyman_enabled", True) else None
        self._holyman_reference_only = True if self._config.get("holyman_reference_only", True) is None else bool(self._config.get("holyman_reference_only", True))

        # 递进推断阈值（改动1）
        thresholds_str = str(self._config.get("inference_thresholds", "5,10,20,40,60,100"))
        self._inference_thresholds = [int(x.strip()) for x in thresholds_str.split(",") if x.strip()]

        # 权重参数
        self._weight_idf = float(self._config.get("weight_idf", 0.4))
        self._weight_burst = float(self._config.get("weight_burst", 0.3))
        self._weight_concentration = float(self._config.get("weight_concentration", 0.3))

        # 构造子组件，传入配置
        self._filter = JargonStatisticalFilter(
            context_keep=self._context_keep,
            window_days=self._window_days,
            jieba_threshold=self._jieba_threshold,
            weight_idf=self._weight_idf,
            weight_burst=self._weight_burst,
            weight_concentration=self._weight_concentration,
        )
        self._inference = JargonInferenceEngine(llm_client, max_context=self._max_context) if llm_client else None
        self._injector = JargonInjector(db, max_inject=int(self._config.get("max_inject", 3)))
        # 挖掘冷却: group_id -> last_mine_ts
        self._last_mine: Dict[str, float] = {}
        # 消息计数: group_id -> count since last mine
        self._msg_count: Dict[str, int] = {}

        # 确保 jargon 表存在
        self._ensure_table()

        # 预热标记（延迟到首次 feed_message 时触发，避免阻塞插件加载）
        self._warmed_up = False

    def _warmup_from_memories(self, days: int = 7, max_rows: int = 20000) -> None:
        """从近 N 天 memories 重放消息，重建内存词频统计。"""
        cutoff = time.time() - days * 86400
        rows = self._db.conn.execute(
            """SELECT content, group_id, sender_id, timestamp FROM memories
               WHERE timestamp >= ? AND group_id IS NOT NULL AND group_id != ''
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, max_rows),
        ).fetchall()
        n = 0
        for content, group_id, sender_id, ts in rows:
            if content and group_id:
                self._filter.feed(content, str(group_id), str(sender_id or ""), timestamp=float(ts or time.time()))
                n += 1
        if n:
            groups = len(self._filter._group_freq)
            logger.info(f"[Jargon] 预热完成：重放 {n} 条消息，覆盖 {groups} 个群的词频")

    def _ensure_table(self) -> None:
        """创建 jargon 表（如不存在）。"""
        try:
            self._db.conn.execute("""
                CREATE TABLE IF NOT EXISTS jargon (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    word TEXT NOT NULL,
                    meaning TEXT DEFAULT '',
                    is_jargon INTEGER DEFAULT NULL,
                    frequency INTEGER DEFAULT 1,
                    confidence REAL DEFAULT 0,
                    is_global INTEGER DEFAULT 0,
                    group_id TEXT NOT NULL,
                    contexts TEXT DEFAULT '[]',
                    created_at INTEGER,
                    updated_at INTEGER,
                    status TEXT DEFAULT 'pending',
                    scope TEXT DEFAULT 'local',
                    source TEXT DEFAULT 'wave_memory',
                    last_infer_freq INTEGER DEFAULT 0,
                    reject_reason TEXT,
                    UNIQUE(group_id, word)
                )
            """)
            cols = {r[1] for r in self._db.conn.execute("PRAGMA table_info(jargon)").fetchall()}
            for col, ddl in {
                "status": "TEXT DEFAULT 'pending'",
                "scope": "TEXT DEFAULT 'local'",
                "source": "TEXT DEFAULT 'wave_memory'",
                "last_infer_freq": "INTEGER DEFAULT 0",
                "reject_reason": "TEXT",
                "source_memory_id": "INTEGER",
                "source_message_ts": "REAL",
                "source_sender_id": "TEXT",
                "source_context": "TEXT DEFAULT '[]'",
                "candidate_type": "TEXT DEFAULT 'jargon'",
            }.items():
                if col not in cols:
                    self._db.conn.execute(f"ALTER TABLE jargon ADD COLUMN {col} {ddl}")
            self._db.conn.execute("CREATE INDEX IF NOT EXISTS idx_jargon_source_memory ON jargon(source_memory_id)")
            self._db.conn.execute("CREATE INDEX IF NOT EXISTS idx_jargon_source_ts ON jargon(group_id, source_message_ts)")
            self._db.conn.commit()
        except Exception as e:
            logger.debug(f"[Jargon] table ensure: {e}")

    # ─── 公开接口 ───

    def feed_message(self, text: str, group_id: str, sender_id: str = "", timestamp: float = None) -> None:
        """喂入一条消息（由 on_message 调用）。"""
        if not self._enabled:
            return
        # 首次调用时触发延迟预热（避免阻塞插件加载）
        if not self._warmed_up:
            self._warmed_up = True
            try:
                self._warmup_from_memories(days=7, max_rows=10000)
            except Exception as e:
                logger.debug(f"[Jargon] warmup skipped: {e}")
        self._filter.feed(text, group_id, sender_id, timestamp=timestamp or time.time())
        self._msg_count[group_id] = self._msg_count.get(group_id, 0) + 1

    def should_mine(self, group_id: str) -> bool:
        """判断是否应该触发挖掘（min_messages 条消息 + mine_cooldown 冷却）。"""
        count = self._msg_count.get(group_id, 0)
        if count < self._min_messages:
            return False
        last = self._last_mine.get(group_id, 0)
        if time.time() - last < self._mine_cooldown:
            return False
        return True

    async def mine(self, group_id: str) -> List[Dict]:
        """执行一次挖掘：统计筛选 + (可选)LLM验证 + LLM 推断。返回新发现的黑话列表。"""
        if not self._enabled:
            return []

        self._msg_count[group_id] = 0
        self._last_mine[group_id] = time.time()

        # Step 1: 统计候选
        candidates = self._filter.get_candidates(group_id, min_freq=self._min_frequency, top_k=self._top_k)
        if not candidates:
            return []

        now = int(time.time())
        routed_candidates = []
        for cand in candidates:
            word = cand.get("word", "")
            source_ctx = self._pick_source_context(cand)
            route = self.classify_candidate(word, group_id, source_ctx, cand.get("contexts") or [])
            cand["_route"] = route
            if route.get("candidate_type") == "person_alias":
                memory_id = self._resolve_source_memory_id(group_id, word, source_ctx)
                self._record_person_alias_fact(group_id, word, source_ctx, memory_id)
                self._record_diverted_candidate(cand, group_id, route, source_ctx, memory_id, now)
                continue
            if route.get("candidate_type") in {"technical_noise", "ordinary_word"}:
                memory_id = self._resolve_source_memory_id(group_id, word, source_ctx)
                self._record_diverted_candidate(cand, group_id, route, source_ctx, memory_id, now)
                continue
            if route.get("candidate_type") == "holyman_reference_hit":
                # Holyman 是广域 reference-only 命中，不写成本地群黑话候选。
                continue
            if route.get("enter_llm"):
                routed_candidates.append(cand)

        candidates = routed_candidates
        if not candidates:
            self._db.conn.commit()
            return []
        logger.info(f"[Jargon] 挖掘 {group_id}: {len(candidates)} 本地候选进入 LLM")

        # Step 1.5: LLM 批量验证（改动3，开关控制）
        if self._llm_validate and self._llm and candidates:
            candidates = await self._llm_validate_candidates(candidates, group_id)
            if not candidates:
                return []
            logger.info(f"[Jargon] LLM 验证通过: {len(candidates)} 候选")

        results = []

        for cand in candidates:
            word = cand["word"]
            contexts = cand.get("contexts") or []
            source_contexts = cand.get("source_contexts") or []
            source_ctx = self._pick_source_context(cand)
            source_memory_id = self._resolve_source_memory_id(group_id, word, source_ctx)
            source_message_ts = source_ctx.get("timestamp")
            source_sender_id = str(source_ctx.get("sender_id") or "")
            source_context_json = json.dumps(source_contexts or [source_ctx] if source_ctx else [], ensure_ascii=False)
            route = cand.get("_route") or self.classify_candidate(word, group_id, source_ctx, contexts)
            candidate_type = route.get("candidate_type") or "local_jargon_candidate"

            # 检查是否已存在
            existing = self._db.conn.execute(
                "SELECT id, frequency, is_jargon, last_infer_freq FROM jargon WHERE group_id = ? AND word = ?",
                (group_id, word),
            ).fetchone()

            if existing:
                row_id, old_freq, old_is_jargon, last_infer_freq = existing
                current_freq = cand["frequency"]
                last_infer_freq = last_infer_freq or 0

                # 改动1：递进重推机制
                if self._should_reinfer(current_freq, last_infer_freq):
                    logger.debug(f"[Jargon] 重推 '{word}': freq {last_infer_freq} → {current_freq}")
                    is_jargon = None
                    meaning = ""
                    confidence = 0.0

                    if self._inference and contexts:
                        try:
                            result = await self._inference.infer(word, contexts)
                            is_jargon = result.get("is_jargon")
                            meaning = result.get("meaning", "")
                            confidence = result.get("confidence", 0.0)
                        except Exception as e:
                            logger.debug(f"[Jargon] reinfer error for '{word}': {e}")

                    status = "confirmed" if is_jargon is True and meaning and confidence >= self._confidence_threshold else ("rejected" if is_jargon is False else "pending")
                    reject_reason = "llm_inference_rejected" if status == "rejected" else None
                    # 更新 DB：frequency + meaning + is_jargon + status + last_infer_freq + reject_reason
                    if reject_reason:
                        self._db.conn.execute(
                            """UPDATE jargon SET frequency = ?, meaning = ?, is_jargon = ?,
                               confidence = ?, status = ?, reject_reason = ?, last_infer_freq = ?, updated_at = ?,
                               source_memory_id = COALESCE(source_memory_id, ?), source_message_ts = COALESCE(source_message_ts, ?),
                               source_sender_id = COALESCE(source_sender_id, ?), source_context = ?, candidate_type = ? WHERE id = ?""",
                            (current_freq, meaning, is_jargon, confidence, status, reject_reason, current_freq, now,
                             source_memory_id, source_message_ts, source_sender_id, source_context_json, candidate_type, row_id),
                        )
                    else:
                        self._db.conn.execute(
                            """UPDATE jargon SET frequency = ?, meaning = ?, is_jargon = ?,
                               confidence = ?, status = ?, reject_reason = NULL, last_infer_freq = ?, updated_at = ?,
                               source_memory_id = COALESCE(source_memory_id, ?), source_message_ts = COALESCE(source_message_ts, ?),
                               source_sender_id = COALESCE(source_sender_id, ?), source_context = ?, candidate_type = ? WHERE id = ?""",
                            (current_freq, meaning, is_jargon, confidence, status, current_freq, now,
                             source_memory_id, source_message_ts, source_sender_id, source_context_json, candidate_type, row_id),
                        )

                    if is_jargon:
                        results.append({"word": word, "meaning": meaning, "confidence": confidence})
                else:
                    # 仅更新频率
                    self._db.conn.execute(
                        """UPDATE jargon SET frequency = ?, updated_at = ?,
                           source_memory_id = COALESCE(source_memory_id, ?), source_message_ts = COALESCE(source_message_ts, ?),
                           source_sender_id = COALESCE(source_sender_id, ?), source_context = ?, candidate_type = COALESCE(candidate_type, ?)
                           WHERE id = ?""",
                        (current_freq, now, source_memory_id, source_message_ts, source_sender_id, source_context_json, candidate_type, row_id),
                    )
                continue

            # 新候选：先尝试 holyman 广域抽象文化参考，再尝试 LLM 推断
            is_jargon = None
            meaning = ""
            confidence = 0.0
            status = "pending"
            scope = "local"
            source = "wave_memory"

            if is_jargon is None and self._inference and contexts:
                try:
                    result = await self._inference.infer(word, contexts)
                    is_jargon = result.get("is_jargon")
                    meaning = result.get("meaning", "")
                    confidence = result.get("confidence", 0.0)
                    if is_jargon is True and meaning and confidence >= self._confidence_threshold:
                        status = "confirmed"
                    elif is_jargon is False:
                        status = "rejected"
                    else:
                        status = "pending"
                except Exception as e:
                    logger.debug(f"[Jargon] inference error for '{word}': {e}")

            reject_reason = "llm_inference_rejected" if status == "rejected" else None

            # 写入 DB（last_infer_freq = 当前频次）
            self._db.conn.execute(
                """INSERT OR IGNORE INTO jargon
                   (word, meaning, is_jargon, frequency, confidence, group_id, contexts,
                    last_infer_freq, created_at, updated_at, status, scope, source, reject_reason,
                    source_memory_id, source_message_ts, source_sender_id, source_context, candidate_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (word, meaning, is_jargon, cand["frequency"], confidence, group_id,
                 json.dumps(contexts, ensure_ascii=False), cand["frequency"], now, now, status, scope, source, reject_reason,
                 source_memory_id, source_message_ts, source_sender_id, source_context_json, candidate_type),
            )

            if is_jargon:
                results.append({"word": word, "meaning": meaning, "confidence": confidence})

        self._db.conn.commit()

        # US-4.5: 跨群检查（同词在 >= 3 群确认 → 全局）
        self._check_global_promotion()

        return results

    def classify_candidate(self, word: str, group_id: str = "", source_ctx: Optional[Dict] = None, contexts: Optional[List[str]] = None) -> Dict[str, Any]:
        """v4.3.0 候选路由：只有本地黑话候选允许进入 LLM。"""
        word = (word or "").strip()
        source_ctx = source_ctx or {}
        contexts = contexts or []
        base = {
            "word": word,
            "candidate_type": "local_jargon_candidate",
            "enter_llm": True,
            "reject_reason": None,
            "source": "wave_memory",
            "scope": "local",
            "meaning": "",
            "confidence": 0.0,
            "reference_only": False,
        }
        if not word:
            return {**base, "candidate_type": "ordinary_word", "enter_llm": False, "reject_reason": "ordinary_word_filtered"}

        sender_id = str(source_ctx.get("sender_id") or "")
        if self._is_known_person_alias(word, group_id, source_ctx):
            return {**base, "candidate_type": "person_alias", "enter_llm": False, "reject_reason": "person_alias_diverted"}
        if self._is_technical_noise_candidate(word):
            return {**base, "candidate_type": "technical_noise", "enter_llm": False, "reject_reason": "technical_noise_filtered"}
        if self._is_ordinary_word_candidate(word):
            return {**base, "candidate_type": "ordinary_word", "enter_llm": False, "reject_reason": "ordinary_word_filtered"}
        if self._is_person_like_candidate(word, sender_id):
            return {**base, "candidate_type": "person_alias", "enter_llm": False, "reject_reason": "person_alias_diverted"}

        holyman_match = self._holyman.match(word, "\n".join(contexts)) if self._holyman else {"matched": False}
        if holyman_match.get("matched"):
            return {
                **base,
                "candidate_type": "holyman_reference_hit",
                "enter_llm": False,
                "source": "holyman_skills",
                "source_layer": holyman_match.get("source_layer") or "phrases",
                "meaning": holyman_match.get("explanation", ""),
                "confidence": float(holyman_match.get("confidence", 0.0) or 0.0),
                "reference_only": True,
                "matched_term": holyman_match.get("term") or word,
            }
        return base

    def _record_diverted_candidate(self, cand: Dict, group_id: str, route: Dict, source_ctx: Dict, memory_id: Optional[int], now: int) -> None:
        """保留分流审计行；默认列表会隐藏这些非黑话候选。"""
        word = str(cand.get("word") or "").strip()
        if not word:
            return
        contexts = cand.get("contexts") or []
        source_contexts = cand.get("source_contexts") or []
        source_message_ts = source_ctx.get("timestamp")
        source_sender_id = str(source_ctx.get("sender_id") or "")
        source_context_json = json.dumps(source_contexts or [source_ctx] if source_ctx else [], ensure_ascii=False)
        candidate_type = route.get("candidate_type") or "ordinary_word"
        reject_reason = route.get("reject_reason") or f"{candidate_type}_filtered"
        self._db.conn.execute(
            """INSERT OR IGNORE INTO jargon
               (word, meaning, is_jargon, frequency, confidence, group_id, contexts,
                last_infer_freq, created_at, updated_at, status, scope, source, reject_reason,
                source_memory_id, source_message_ts, source_sender_id, source_context, candidate_type)
               VALUES (?, ?, 0, ?, 0, ?, ?, 0, ?, ?, 'rejected', 'local', 'wave_memory',
                       ?, ?, ?, ?, ?, ?)""",
            (word, route.get("meaning", ""), cand.get("frequency", 1), group_id, json.dumps(contexts, ensure_ascii=False), now, now,
             reject_reason, memory_id, source_message_ts, source_sender_id, source_context_json, candidate_type),
        )
        self._db.conn.execute(
            """UPDATE jargon SET frequency = ?, status = 'rejected', is_jargon = 0,
               reject_reason = ?, source_memory_id = COALESCE(source_memory_id, ?),
               source_message_ts = COALESCE(source_message_ts, ?), source_sender_id = COALESCE(source_sender_id, ?),
               source_context = ?, candidate_type = ?, updated_at = ?
               WHERE group_id = ? AND word = ? AND COALESCE(status, 'pending') != 'confirmed'""",
            (cand.get("frequency", 1), reject_reason, memory_id, source_message_ts, source_sender_id,
             source_context_json, candidate_type, now, group_id, word),
        )

    @staticmethod
    def _is_technical_noise_candidate(word: str) -> bool:
        word = (word or "").strip()
        lower_word = word.lower()
        if lower_word in _TECHNICAL_NOISE_WORDS:
            return True
        if re.match(r"^https?://", word, re.I):
            return True
        if re.search(r"[/\\]", word) and re.search(r"\.[A-Za-z0-9]{1,8}$", word):
            return True
        if re.fullmatch(r"[a-fA-F0-9]{7,64}", word):
            return True
        if re.fullmatch(r"v?\d+(?:\.\d+){1,3}", word):
            return True
        return False

    @staticmethod
    def _is_ordinary_word_candidate(word: str) -> bool:
        word = (word or "").strip()
        if not word or "@" in word:
            return True
        if len(word) < 2 or len(word) > 12:
            return True
        if re.match(r"^[\d\s.]+$", word):
            return True
        if re.match(r"^[^\w\u4e00-\u9fff]+$", word):
            return True
        if re.search(r"[，。！？!?、；;：:\s]", word):
            return True
        if re.match(r"^[A-Za-z]+$", word) and len(word) > 6:
            return True
        if re.match(r"^\[.+\]$", word):
            return True
        return word in _ORDINARY_WORDS

    def _table_exists(self, table: str) -> bool:
        try:
            row = self._db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return row is not None
        except Exception:
            return False

    def _table_columns(self, table: str) -> set[str]:
        try:
            return {str(r[1]) for r in self._db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def _is_known_person_alias(self, word: str, group_id: str, source_ctx: Dict) -> bool:
        word = (word or "").strip()
        if not word:
            return False
        sender_id = str(source_ctx.get("sender_id") or "")
        sender_name = str(source_ctx.get("sender_name") or "").strip()
        if sender_name and word == sender_name:
            return True
        checks = [
            ("memories", "sender_name", "group_id = ? AND sender_name = ?", (group_id, word)),
            ("user_profiles", "nickname", "group_id = ? AND nickname = ?", (group_id, word)),
            ("person_registry", "display_name", "display_name = ?", (word,)),
        ]
        for table, required_col, where_sql, params in checks:
            if self._table_exists(table) and required_col in self._table_columns(table):
                try:
                    row = self._db.conn.execute(f"SELECT 1 FROM {table} WHERE {where_sql} LIMIT 1", params).fetchone()
                    if row:
                        return True
                except Exception:
                    pass
        if self._table_exists("facts"):
            cols = self._table_columns("facts")
            try:
                if {"fact_type", "object"}.issubset(cols):
                    row = self._db.conn.execute(
                        "SELECT 1 FROM facts WHERE fact_type = 'PERSON_ALIAS' AND (object = ? OR subject = ?) LIMIT 1",
                        (word, sender_id or word),
                    ).fetchone()
                    if row:
                        return True
                elif {"fact_type", "obj"}.issubset(cols):
                    row = self._db.conn.execute(
                        "SELECT 1 FROM facts WHERE fact_type = 'PERSON_ALIAS' AND (obj = ? OR subject = ?) LIMIT 1",
                        (word, sender_id or word),
                    ).fetchone()
                    if row:
                        return True
            except Exception:
                pass
        return False

    @staticmethod
    def _should_filter_candidate(word: str) -> bool:
        """过滤明显不是黑话的候选，减少昵称/句子污染。"""
        word = (word or "").strip()
        if not word or "@" in word:
            return True
        if len(word) < 2 or len(word) > 12:
            return True
        return JargonService._is_technical_noise_candidate(word) or JargonService._is_ordinary_word_candidate(word)

    @staticmethod
    def _is_person_like_candidate(word: str, sender_id: str = "") -> bool:
        """识别疑似人名、昵称或 ID，保守分流到人物事实。"""
        word = (word or "").strip()
        if not word:
            return False
        if sender_id and word == str(sender_id):
            return True
        if re.match(r"^[A-Za-z][A-Za-z0-9_\-]{2,16}$", word) and not re.match(r"^[A-Z0-9]{2,6}$", word):
            return True
        # 常见 2-4 字中文姓名/昵称形态：不直接确认成黑话，交给 facts 保守记录。
        if re.match(r"^[\u4e00-\u9fff]{2,4}$", word):
            # 复姓
            compound_surnames = {"欧阳", "慕容", "上官", "夏侯", "诸葛", "司马", "司徒", "令狐", "独孤", "南宫", "轩辕", "尉迟", "公孙", "长孙", "端木"}
            if len(word) == 4 and word[:2] in compound_surnames:
                return True
            if len(word) == 3 and word[:2] in compound_surnames:
                return True
            # 单姓
            surname = word[0]
            common_surnames = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹"
            return surname in common_surnames and len(word) <= 3
        return False

    def _pick_source_context(self, cand: Dict) -> Dict:
        """从候选上下文中选择一个最可信的源消息片段。"""
        source_contexts = cand.get("source_contexts") or []
        if source_contexts:
            return source_contexts[0] if isinstance(source_contexts[0], dict) else {"content": str(source_contexts[0])}
        contexts = cand.get("contexts") or []
        return {"content": contexts[0]} if contexts else {}

    def _resolve_source_memory_id(self, group_id: str, word: str, source_ctx: Dict) -> Optional[int]:
        """按时间邻域和内容包含关系回填原始 memory id。"""
        try:
            ts = float(source_ctx.get("timestamp") or 0)
            sender_id = str(source_ctx.get("sender_id") or "")
            content = str(source_ctx.get("content") or "")
            if not ts:
                return None
            like_text = f"%{word}%" if word else f"%{content[:40]}%"
            rows = self._db.conn.execute(
                """SELECT id, timestamp, content FROM memories
                   WHERE group_id = ? AND timestamp BETWEEN ? AND ?
                     AND (? = '' OR sender_id = ?)
                     AND content LIKE ?
                   ORDER BY ABS(timestamp - ?) ASC LIMIT 1""",
                (group_id, ts - 30, ts + 30, sender_id, sender_id, like_text, ts),
            ).fetchall()
            if rows:
                return int(rows[0][0])
        except Exception as e:
            logger.debug(f"[Jargon] resolve source memory skipped: {e}")
        return None

    def _record_person_alias_fact(self, group_id: str, word: str, source_ctx: Dict, memory_id: Optional[int]) -> None:
        """把疑似人名/昵称分流为人物事实，避免污染黑话库。"""
        sender_id = str(source_ctx.get("sender_id") or "")
        if not sender_id or not word:
            return
        try:
            self._db.insert_fact(
                subject=sender_id,
                predicate="alias_or_name",
                obj=word,
                group_id=group_id,
                source_memory_id=memory_id,
                confidence=0.6,
                fact_type="PERSON_ALIAS",
            )
        except Exception as e:
            logger.debug(f"[Jargon] person alias fact skipped: {e}")

    def _should_reinfer(self, current_freq: int, last_infer_freq: int) -> bool:
        """判断是否需要重新推断（改动1：递进重推机制）。"""
        for threshold in self._inference_thresholds:
            if last_infer_freq < threshold <= current_freq:
                return True
        return False

    async def _llm_validate_candidates(self, candidates: List[Dict], group_id: str) -> List[Dict]:
        """改动3：LLM 批量验证候选词是否真是黑话。"""
        # 构建近期聊天片段
        all_contexts = []
        for cand in candidates:
            all_contexts.extend(cand.get("contexts", []))
        chat_snippet = "\n".join(f"- {c}" for c in all_contexts[:20])

        term_list = ", ".join(cand["word"] for cand in candidates)

        validate_prompt = f"""**近期聊天片段**
{chat_snippet}

**候选词列表**
{term_list}

请判断以上候选词中，哪些是该群组的黑话/俚语/暗语/缩写。

必须同时满足：
- 脱离该群组语境后普通人无法理解
- 在近期聊天中有明确上下文支撑
- 不是普通词、昵称、人名、品牌名

以JSON数组输出确认是黑话的词条：["词1", "词2"]
如果没有，输出空数组 []"""

        try:
            import re
            response = await self._llm.text_chat(prompt=validate_prompt)
            if not response or not response.completion_text:
                return []  # fail-closed：LLM 失败不新增候选
            text = response.completion_text.strip()
            # 提取 JSON 数组
            text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
            match = re.search(r'\[[\s\S]*?\]', text)
            if match:
                validated_words = set(json.loads(match.group()))
                return [c for c in candidates if c["word"] in validated_words]
        except Exception as e:
            logger.debug(f"[Jargon] LLM validate error: {e}")

        return []  # fail-closed：解析失败不新增候选

    def get_injection(self, text: str, group_id: str) -> str:
        """获取黑话注入文本 (US-4.3)。"""
        if not self._enabled:
            return ""
        return self._injector.get_injection(text, group_id)

    def get_last_injection_items(self) -> List[Dict]:
        """返回最近一次黑话注入命中的结构化观测条目。"""
        getter = getattr(self._injector, "get_last_injection_items", None)
        return getter() if callable(getter) else []

    def _check_global_promotion(self) -> None:
        """US-4.5: 同词在 >= N 群确认 → 自动全局化。"""
        try:
            threshold = self._global_threshold
            rows = self._db.conn.execute(
                f"""SELECT word, COUNT(DISTINCT group_id) as cnt
                   FROM jargon WHERE is_jargon = 1 AND is_global = 0
                   AND COALESCE(status, 'pending') = 'confirmed'
                   GROUP BY word HAVING cnt >= ?""",
                (threshold,),
            ).fetchall()
            if rows:
                now = int(time.time())
                for word, cnt in rows:
                    self._db.conn.execute(
                        "UPDATE jargon SET is_global = 1, updated_at = ? WHERE word = ? AND is_jargon = 1 AND COALESCE(status, 'pending') = 'confirmed'",
                        (now, word),
                    )
                    logger.info(f"[Jargon] 全局化: '{word}' (确认群数: {cnt})")
                self._db.conn.commit()
        except Exception as e:
            logger.debug(f"[Jargon] global promotion error: {e}")
