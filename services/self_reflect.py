"""SelfReflect — 白真真自省循环

检测群友对白真真回复的纠正信号，触发时从 book_lore 搜索相关知识并内化。
集成到 MetaThinking 后续流程中。
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections import deque
from typing import Optional

import numpy as np

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex
from ..engine.book_lore_index import BookLoreIndex
from .llm_fallback import LLMFallbackClient


# 纠正信号正则（群友常见的纠错方式）
CORRECTION_PATTERNS = [
    re.compile(r'(你说错|不是这样|你搞错|你弄错|不对吧|说反了|记错了)', re.I),
    re.compile(r'(白真真.*不[是会对]|小白.*不[是会对]|bzz.*不[是会对])', re.I),
    re.compile(r'(原文[是写说]|书[里中].*[是写说]|作者[说写])', re.I),
    re.compile(r'(你不懂|你没看|你没读|没看过原文)', re.I),
    re.compile(r'(其实是|实际上是|应该是|正确的是)', re.I),
]

# 内化纠正知识的 prompt
CORRECT_PROMPT = """你是白真真。你刚才说了一些不太准确的话，有人纠正了你。

你说的：{bot_reply}
别人纠正：{correction}
相关知识：{knowledge}

现在用你自己的话，一两句话，把正确的理解记在心里。
要求：
- 第一人称
- 像自言自语一样，不是在回应别人
- 不要道歉或承认错误，直接表达正确的认知
- 不要超过80字
- 直接输出内容"""


class SelfReflectService:
    """白真真自省服务：检测纠正信号 → 学习修正。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service,
        llm_client: LLMFallbackClient,
        book_lore_index: Optional[BookLoreIndex],
        lore_db_path: str,
        cooldown_seconds: float = 300.0,  # 同一话题 5 分钟内不重复触发
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.llm = llm_client
        self.book_lore_index = book_lore_index
        self.lore_db_path = lore_db_path
        self.cooldown = cooldown_seconds

        # 记录白真真最近的回复（用于检测纠正）
        self._recent_replies: deque = deque(maxlen=10)
        # 冷却记录：{topic_hash: timestamp}
        self._cooldown_map: dict[str, float] = {}
        self._reflect_count = 0

    def record_reply(self, reply_text: str, group_id: str):
        """记录白真真的一次回复，供后续检测纠正。"""
        self._recent_replies.append({
            "text": reply_text,
            "group_id": group_id,
            "timestamp": time.time(),
        })

    async def check_correction(self, message: str, sender_name: str, group_id: str) -> bool:
        """检查一条群消息是否是对白真真的纠正。如果是，触发学习。

        Returns: True 如果触发了学习
        """
        # 只检查最近 60 秒内白真真有回复的群
        now = time.time()
        recent_reply = None
        for reply in reversed(self._recent_replies):
            if reply["group_id"] == group_id and (now - reply["timestamp"]) < 60:
                recent_reply = reply
                break

        if not recent_reply:
            return False

        # 检测纠正信号
        is_correction = any(p.search(message) for p in CORRECTION_PATTERNS)
        if not is_correction:
            return False

        # 冷却检查
        topic_key = recent_reply["text"][:30]
        if topic_key in self._cooldown_map and (now - self._cooldown_map[topic_key]) < self.cooldown:
            return False

        self._cooldown_map[topic_key] = now

        # 触发学习
        try:
            success = await self._learn_from_correction(
                bot_reply=recent_reply["text"],
                correction=message,
            )
            if success:
                self._reflect_count += 1
                logger.info(f"[SelfReflect] Learned from correction #{self._reflect_count}: {message[:50]}...")
            return success
        except Exception as e:
            logger.warning(f"[SelfReflect] Learning failed: {e}")
            return False

    async def _learn_from_correction(self, bot_reply: str, correction: str) -> bool:
        """从纠正中学习：搜索相关知识 → 内化为记忆。"""
        # 1. 用纠正内容搜索 book_lore
        knowledge = ""
        if self.book_lore_index:
            combined_text = f"{bot_reply} {correction}"
            vec = await self.embedding.get_embedding(combined_text)
            if vec is not None:
                # 搜社区报告
                hits = self.book_lore_index.search_communities(vec, k=2)
                if hits:
                    conn = sqlite3.connect(self.lore_db_path)
                    for cid, score in hits:
                        if score >= 0.3:
                            row = conn.execute(
                                "SELECT title, summary FROM book_communities WHERE id = ?",
                                (cid,)
                            ).fetchone()
                            if row:
                                knowledge += f"{row[0]}：{row[1][:200]}\n"
                    conn.close()

        if not knowledge:
            knowledge = "（无额外参考）"

        # 2. LLM 内化
        prompt = CORRECT_PROMPT.format(
            bot_reply=bot_reply[:200],
            correction=correction[:200],
            knowledge=knowledge[:500],
        )
        resp = await self.llm.text_chat(prompt=prompt)
        text = resp.completion_text.strip()

        # 清理
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        for prefix in ["白真真：", "白真真:", "内心：", "内心:"]:
            if text.startswith(prefix):
                text = text[len(prefix):]
        text = text.strip()

        if len(text) < 8 or len(text) > 150:
            return False

        # 3. 去重
        mem_vec = await self.embedding.get_embedding(text)
        if mem_vec is None:
            return False

        # 简单去重：搜已有记忆（cosine distance < 0.12 → similarity > 0.88）
        results = self.memory_index.search(mem_vec, k=3)
        for _, dist in results:
            if dist <= 0.12:
                return False

        # 4. 写入 bzz_evolution
        mem_id = self.db.add_memory(
            group_id="__bzz_evolution__",
            content=text,
            vector=mem_vec,
            sender_id="1336495069",
            sender_name="白真真",
            importance=1.5,  # 纠正学习的重要度更高
            source="bzz_evolution",
        )
        self.memory_index.add([mem_id], mem_vec.reshape(1, -1))
        return True

    def cleanup_cooldown(self):
        """清理过期的冷却记录。"""
        now = time.time()
        expired = [k for k, t in self._cooldown_map.items() if (now - t) > self.cooldown * 2]
        for k in expired:
            del self._cooldown_map[k]

    @property
    def stats(self) -> dict:
        return {
            "reflect_count": self._reflect_count,
            "recent_replies_buffered": len(self._recent_replies),
        }
