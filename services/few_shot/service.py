"""Few-Shot 风格学习服务 (US-5.1 ~ US-5.4)

功能:
- 从 bot 历史回复中提取高质量风格范例
- LLM 请求时注入 2-3 条已批准 few-shot
- 风格漂移检测
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger

from ..identity_safety import is_identity_contamination


# ─── LLM Prompt ───

_EVALUATE_STYLE_PROMPT = """你是一个对话风格分析专家。
请评估以下 bot 回复是否具有鲜明的个人风格特征（语气、用词、句式习惯等）。

Bot 回复：
{reply}

请评分（0.0~1.0），0 表示毫无风格特征，1 表示风格极其鲜明。
只返回一个 JSON：{{"score": 0.7, "traits": ["特征1", "特征2"]}}"""

_DRIFT_CHECK_PROMPT = """你是一个对话风格一致性分析师。

风格范例库：
{examples}

最新回复：
{recent}

请判断最新回复与范例库的风格是否一致。
返回 JSON：{{"similarity": 0.8, "drift_detected": false, "reason": "..."}}"""


_AGGRESSIVE_STYLE_RE = re.compile(
    r"(怼回去|狠狠怼|别客气|骂回去|反击|嘴臭|阴阳怪气|傻逼|脑残|滚|nmsl|你妈|操你|fuck\s*you)",
    re.IGNORECASE,
)


class FewShotService:
    """Few-Shot 风格学习主服务。"""

    def __init__(self, db: Any, llm_client: Any = None, embedding_service: Any = None, enabled: bool = True, config: dict = None):
        self._db = db
        self._llm = llm_client
        self._embedding = embedding_service
        self._enabled = enabled
        self._config = config or {}
        self._last_extract: float = 0  # 上次提取时间
        self._last_injected_ids: List[int] = []  # 避免连续注入同一条

        # 从配置读取参数
        self._min_score = float(self._config.get("min_score", 0.7))
        self._max_inject = int(self._config.get("max_inject", 3))
        self._drift_threshold = float(self._config.get("drift_threshold", 0.5))
        self._ensure_table()

    def _ensure_table(self) -> None:
        """创建 few_shot_examples 表。"""
        try:
            self._db.conn.execute("""
                CREATE TABLE IF NOT EXISTS few_shot_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    score REAL DEFAULT 0,
                    traits TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    bot_id TEXT DEFAULT '',
                    created_at INTEGER,
                    approved_at INTEGER DEFAULT NULL
                )
            """)
            self._db.conn.commit()
        except Exception as e:
            logger.debug(f"[FewShot] table ensure: {e}")

    # ─── US-5.1: 提取风格范例 ───

    async def extract_candidates(self, bot_id: str = "") -> List[Dict]:
        """从最近 7 天 bot 回复中提取 top 10 候选。每天最多执行一次。"""
        if not self._enabled or not self._llm:
            return []

        now = time.time()
        if now - self._last_extract < 86400:  # 24h 冷却
            return []
        self._last_extract = now

        # 获取最近 7 天的 bot 回复
        seven_days_ago = int(now) - 7 * 86400
        rows = self._db.conn.execute(
            """SELECT id, content FROM memories
               WHERE source IN ('bot_reply', 'bzz_experience', 'bzz_evolution')
               AND timestamp > ? AND LENGTH(content) >= 20
               ORDER BY RANDOM() LIMIT 50""",
            (seven_days_ago,),
        ).fetchall()

        if not rows:
            return []

        # LLM 评估风格代表性
        candidates = []
        for mem_id, content in rows:
            try:
                if not self._is_healthy_example(content):
                    continue
                result = await self._evaluate_style(content)
                score = result.get("score", 0)
                if score >= self._min_score:
                    candidates.append({
                        "content": content,
                        "score": score,
                        "traits": result.get("traits", []),
                    })
            except Exception:
                continue

            if len(candidates) >= 10:
                break

        # 写入 DB
        now_ts = int(time.time())
        for c in candidates:
            self._db.conn.execute(
                """INSERT OR IGNORE INTO few_shot_examples (content, score, traits, status, bot_id, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (c["content"], c["score"], json.dumps(c["traits"], ensure_ascii=False), bot_id, now_ts),
            )
        self._db.conn.commit()

        logger.info(f"[FewShot] 提取了 {len(candidates)} 条风格候选")
        return candidates

    # ─── US-5.2: 注入 few-shot ───

    def get_injection(self, bot_id: str = "", max_items: int = None) -> str:
        """获取 2-3 条已批准 few-shot 注入文本。"""
        if not self._enabled:
            return ""
        if max_items is None:
            max_items = self._max_inject

        rows = self._db.conn.execute(
            """SELECT id, content FROM few_shot_examples
               WHERE status = 'approved' AND (bot_id = ? OR bot_id = '')
               ORDER BY score DESC LIMIT 20""",
            (bot_id,),
        ).fetchall()

        if not rows:
            return ""

        healthy_rows = [(r[0], r[1]) for r in rows if self._is_healthy_example(r[1])]
        if not healthy_rows:
            self._last_injected_ids = []
            return ""

        # 不连续重复同一条
        available = [(r[0], r[1]) for r in healthy_rows if r[0] not in self._last_injected_ids]
        if len(available) < max_items:
            available = healthy_rows  # 库太小时允许重复

        selected = random.sample(available, min(max_items, len(available)))
        self._last_injected_ids = [s[0] for s in selected]

        examples = "\n".join(f"- {s[1]}" for s in selected)
        return f"<style_examples>\n{examples}\n</style_examples>"

    # ─── US-5.4: 风格漂移检测 ───

    async def check_drift(self, recent_reply: str, bot_id: str = "") -> Optional[Dict]:
        """检查最新回复是否风格漂移。返回 None 表示无漂移。"""
        if not self._enabled or not self._llm:
            return None

        # 获取已批准范例
        rows = self._db.conn.execute(
            """SELECT content FROM few_shot_examples
               WHERE status = 'approved' AND (bot_id = ? OR bot_id = '')
               ORDER BY score DESC LIMIT 5""",
            (bot_id,),
        ).fetchall()

        if len(rows) < 3:
            return None  # 范例太少无法判断

        examples_text = "\n".join(f"- {r[0]}" for r in rows)
        prompt = _DRIFT_CHECK_PROMPT.format(examples=examples_text, recent=recent_reply)

        try:
            result = await self._call_llm(prompt)
            similarity = result.get("similarity", 1.0)
            if similarity < self._drift_threshold:
                logger.warning(f"[FewShot] 风格漂移检测: similarity={similarity:.2f}")
                return {"similarity": similarity, "drift_detected": True, "reason": result.get("reason", "")}
        except Exception:
            pass
        return None

    # ─── 内部方法 ───

    def _is_healthy_example(self, content: str) -> bool:
        """Return True only for style examples that are safe to re-inject."""
        text = str(content or "").strip()
        if not text:
            return False
        if is_identity_contamination(text):
            return False
        if _AGGRESSIVE_STYLE_RE.search(text):
            return False
        return True

    async def _evaluate_style(self, reply: str) -> Dict:
        prompt = _EVALUATE_STYLE_PROMPT.format(reply=reply[:500])
        return await self._call_llm(prompt)

    async def _call_llm(self, prompt: str) -> Dict:
        import re
        response = await self._llm.text_chat(prompt=prompt)
        if not response or not response.completion_text:
            return {}
        text = response.completion_text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            return json.loads(match.group())
        return {}
