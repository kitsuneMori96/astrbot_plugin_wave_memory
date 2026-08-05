"""PairSimilarityService — 标签对相似度预计算 + O(1) Map 查表"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from astrbot.api import logger


class PairSimilarityService:
    """标签对语义相似度预计算服务。

    定期从 tag 向量计算 pair similarity 并缓存到内存 map + DB。
    优化：内存使用受限大小的懒加载缓存，防200万条大表撑爆内存。
    """

    def __init__(self, db, refresh_interval: float = 1800.0):
        """
        Args:
            db: WaveMemoryDB facade
            refresh_interval: 刷新间隔（秒），默认 30 分钟
        """
        self.db = db
        self.refresh_interval = refresh_interval
        self._cache: dict[tuple[int, int], float] = {}
        self._last_refresh: float = 0
        self._max_cache_size = 20000 # 限制内存字典最大容量，防止大数据下撑爆内存

    def get_similarity(self, tag_a: int, tag_b: int) -> float:
        """查表获取标签对相似度。未命中从 DB 兜底。"""
        if tag_a == tag_b:
            return 1.0
        key = (min(tag_a, tag_b), max(tag_a, tag_b))
        
        # 1. 尝试从内存缓存获取
        sim = self._cache.get(key, None)
        if sim is not None:
            return sim
            
        # 2. 缓存未命中，去 DB 查
        try:
            row = self.db.conn.execute(
                "SELECT similarity FROM tag_pair_similarity WHERE tag_a=? AND tag_b=?",
                (key[0], key[1])
            ).fetchone()
            sim = row[0] if row else 0.0
        except Exception:
            sim = 0.0
            
        # 3. 写入内存缓存并维护容量上限
        if len(self._cache) >= self._max_cache_size:
            pop_key = next(iter(self._cache))
            self._cache.pop(pop_key, None)
        self._cache[key] = sim
        return sim

    def refresh_if_needed(self):
        """按需刷新缓存。"""
        now = time.time()
        if now - self._last_refresh < self.refresh_interval:
            return
        self._refresh()

    def _refresh(self):
        """从 DB 加载或重算相似度。"""
        start = time.time()

        # 优化：不把 200 万行数据全拉到 Python 内存，只做轻量行数检测
        try:
            row = self.db.conn.execute("SELECT COUNT(*) FROM tag_pair_similarity").fetchone()
            if row and row[0] > 0:
                self._cache.clear() # 依靠 get_similarity 中的懒加载，清空本地缓存以释放内存
                self._last_refresh = time.time()
                logger.info(f"[WaveMemory] PairSimilarity database checked (rows: {row[0]}), memory lazy loading enabled.")
                return
        except Exception:
            pass

        # DB 无数据，从向量计算
        self._compute_and_persist()

    def _compute_and_persist(self, max_tags: int = 2000):
        """计算 top-N 标签的 pair similarity 并持久化。"""
        start = time.time()

        # 优化：SQL 层做 Limit，不拉取 12 万条后再做过滤，极大减少临时内存和计算开销
        tag_data = self.db.get_all_tag_vectors(limit=max_tags)
        if not tag_data or len(tag_data) < 2:
            self._last_refresh = time.time()
            return

        # 构建向量矩阵
        ids = [t[0] for t in tag_data]
        vecs = np.array([t[2] for t in tag_data], dtype=np.float32)

        # 归一化
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms < 1e-8, 1.0, norms)
        vecs_normed = vecs / norms

        # 批量余弦相似度 (只存上三角)
        new_cache = {}
        batch_params = []
        now = time.time()

        # 分块计算避免内存爆炸
        n = len(ids)
        chunk_size = 500
        for i_start in range(0, n, chunk_size):
            i_end = min(i_start + chunk_size, n)
            sim_block = vecs_normed[i_start:i_end] @ vecs_normed.T  # (chunk, n)
            for local_i, global_i in enumerate(range(i_start, i_end)):
                for global_j in range(global_i + 1, n):
                    sim = float(sim_block[local_i, global_j])
                    if sim > 0.1:  # 只存有意义的相似度
                        key = (ids[global_i], ids[global_j])
                        new_cache[key] = sim
                        batch_params.append((ids[global_i], ids[global_j], sim, now))

        self._cache = new_cache

        # 持久化到 DB（批量写入）
        if batch_params:
            try:
                self.db.conn.execute("DELETE FROM tag_pair_similarity")
                self.db.conn.executemany(
                    "INSERT INTO tag_pair_similarity (tag_a, tag_b, similarity, computed_at) VALUES (?, ?, ?, ?)",
                    batch_params,
                )
                self.db.conn.commit()
            except Exception as e:
                logger.warning(f"[WaveMemory] PairSimilarity persist failed: {e}")

        elapsed = time.time() - start
        self._last_refresh = time.time()
        logger.info(
            f"[WaveMemory] PairSimilarity computed: {len(new_cache)} pairs from {n} tags, {elapsed:.2f}s"
        )

    def force_refresh(self):
        """强制刷新。"""
        self._last_refresh = 0
        self._refresh()
