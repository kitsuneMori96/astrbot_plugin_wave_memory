"""StudyService — 白真真自主学习循环

每 6 小时从 book_lore 社区报告中采样高价值世界观知识，
用 LLM 以白真真第一人称口吻"内化"为经历记忆，写入 bzz_evolution。

这样白真真对世界观的理解从"检索注入"逐步变成"自己的记忆"。
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import time
from typing import Optional

import numpy as np

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex
from .llm_fallback import LLMFallbackClient


INTERNALIZE_PROMPT = """你是白真真。以下是你世界里的一个常识性知识。

---
{knowledge}
---

用你自己的方式，一两句话，想一下跟这个相关的你的经历或看法。
要求：
- 第一人称
- 像心里默默想的那样，不是在解释给别人听
- 不要评价这个知识"好不好""合不合理"——这是你从小就知道的东西
- 不要超过100字
- 不要用引号框起来
- 直接输出内容，不要前缀"""


class StudyService:
    """白真真自主学习服务。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service,
        llm_client: LLMFallbackClient,
        lore_db_path: str,
        study_interval_hours: float = 6.0,
        max_new_per_cycle: int = 2,
        dedup_threshold: float = 0.85,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.llm = llm_client
        self.lore_db_path = lore_db_path
        self.study_interval = study_interval_hours * 3600
        self.max_new_per_cycle = max_new_per_cycle
        self.dedup_threshold = dedup_threshold
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._study_count = 0

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._study_loop())
        logger.info("[WaveMemory] StudyService started (interval=%.1fh)", self.study_interval / 3600)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _study_loop(self):
        """定时学习。首次启动延迟 5 分钟后开始。"""
        await asyncio.sleep(300)  # 等插件完全加载
        while self._running:
            try:
                result = await self.study_once()
                if result["new_memories"] > 0:
                    logger.info(
                        "[StudyService] Cycle %d: internalized %d memories from %d candidates",
                        self._study_count, result["new_memories"], result["candidates"]
                    )
                self._study_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[StudyService] Error: {e}")
            await asyncio.sleep(self.study_interval)

    async def study_once(self) -> dict:
        """执行一次学习循环。"""
        # 1. 从 book_lore 采样高价值社区报告
        candidates = self._sample_communities(count=5)
        if not candidates:
            return {"candidates": 0, "new_memories": 0}

        new_count = 0
        for title, summary in candidates:
            if new_count >= self.max_new_per_cycle:
                break

            # 2. 去重检查：这个知识是否已经被内化过
            knowledge_text = f"{title}：{summary[:300]}"
            knowledge_vec = await self.embedding.get_embedding(knowledge_text)
            if knowledge_vec is None:
                continue

            if self._is_duplicate(knowledge_vec):
                continue

            # 3. LLM 内化
            internalized = await self._internalize(knowledge_text)
            if not internalized or len(internalized) < 10 or len(internalized) > 200:
                continue

            # 4. 生成内化记忆的向量
            mem_vec = await self.embedding.get_embedding(internalized)
            if mem_vec is None:
                continue

            # 5. 再次去重（用生成文本的向量）
            if self._is_duplicate(mem_vec):
                continue

            # 6. 写入 bzz_evolution
            mem_id = self.db.add_memory(
                group_id="__bzz_evolution__",
                content=internalized,
                vector=mem_vec,
                sender_id="1336495069",
                sender_name="白真真",
                importance=1.2,
                source="bzz_evolution",
            )

            # 7. 加入向量索引
            self.memory_index.add([mem_id], mem_vec.reshape(1, -1))

            new_count += 1
            logger.debug(f"[StudyService] Internalized: {internalized[:50]}...")

        return {"candidates": len(candidates), "new_memories": new_count}

    def _sample_communities(self, count: int = 5) -> list[tuple[str, str]]:
        """从 book_lore 社区报告中按 rank 权重采样。"""
        try:
            conn = sqlite3.connect(self.lore_db_path)
            # 取 rank >= 7 的高价值社区报告
            rows = conn.execute(
                "SELECT title, summary, rank FROM book_communities WHERE rank >= 7.0 AND summary != '' ORDER BY RANDOM() LIMIT ?",
                (count * 3,)  # 多取一些，后面会去重过滤
            ).fetchall()
            conn.close()

            if not rows:
                return []

            # 按 rank 加权采样
            weights = [r[2] ** 2 for r in rows]  # rank 越高越容易被选中
            total = sum(weights)
            weights = [w / total for w in weights]

            selected_indices = []
            for _ in range(min(count, len(rows))):
                r = random.random()
                cumulative = 0
                for i, w in enumerate(weights):
                    cumulative += w
                    if r <= cumulative and i not in selected_indices:
                        selected_indices.append(i)
                        break
                else:
                    # fallback: 取第一个未选的
                    for i in range(len(rows)):
                        if i not in selected_indices:
                            selected_indices.append(i)
                            break

            return [(rows[i][0], rows[i][1]) for i in selected_indices]

        except Exception as e:
            logger.warning(f"[StudyService] Failed to sample communities: {e}")
            return []

    def _is_duplicate(self, vec: np.ndarray) -> bool:
        """检查向量是否与已有记忆过于相似（cosine distance < threshold）。"""
        try:
            results = self.memory_index.search(vec, k=5)
            # distance = 1 - cosine_similarity, so threshold 0.85 sim → distance 0.15
            max_dist = 1.0 - self.dedup_threshold
            for mem_id, dist in results:
                if dist <= max_dist:
                    return True
            return False
        except Exception:
            return False

    async def _internalize(self, knowledge: str) -> Optional[str]:
        """用 LLM 以白真真口吻内化知识。"""
        try:
            prompt = INTERNALIZE_PROMPT.format(knowledge=knowledge)
            resp = await self.llm.text_chat(prompt=prompt)
            text = resp.completion_text.strip()

            # 清理常见 LLM 输出噪音
            # 去掉开头的引号
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]

            # 去掉 "白真真：" 或 "内心：" 前缀
            for prefix in ["白真真：", "白真真:", "内心：", "内心:"]:
                if text.startswith(prefix):
                    text = text[len(prefix):]

            return text.strip()

        except Exception as e:
            logger.debug(f"[StudyService] LLM internalize failed: {e}")
            return None

    @property
    def stats(self) -> dict:
        """返回学习统计。"""
        return {
            "study_cycles": self._study_count,
            "running": self._running,
            "interval_hours": self.study_interval / 3600,
        }
