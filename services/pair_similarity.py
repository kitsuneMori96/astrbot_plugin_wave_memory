"""PairSimilarityService — 标签对相似度预计算 + O(1) Map 查表"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from astrbot.api import logger


class PairSimilarityService:
    """标签对语义相似度预计算服务。

    定期从 tag 向量计算 pair similarity 并缓存到内存 map + DB。
    查询时 O(1) dict 查表。
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

    def get_similarity(self, tag_a: int, tag_b: int) -> float:
        """O(1) 查表获取标签对相似度。未命中返回 0.0。"""
        if tag_a == tag_b:
            return 1.0
        key = (min(tag_a, tag_b), max(tag_a, tag_b))
        return self._cache.get(key, 0.0)

    def refresh_if_needed(self):
        """按需刷新缓存。"""
        now = time.time()
        if now - self._last_refresh < self.refresh_interval:
            return
        self._refresh()

    def _refresh(self):
        """从 DB 加载或重算相似度。"""
        start = time.time()

        # 先尝试从 DB 加载
        try:
            rows = self.db.conn.execute(
                "SELECT tag_id_a, tag_id_b, similarity FROM tag_pair_similarity"
            ).fetchall()
            if rows:
                self._cache = {(r[0], r[1]): r[2] for r in rows}
                self._last_refresh = time.time()
                logger.debug(f"[WaveMemory] PairSimilarity loaded from DB: {len(rows)} pairs")
                return
        except Exception:
            pass

        # DB 无数据，从向量计算
        self._compute_and_persist()

    def _compute_and_persist(self, max_tags: int = 2000):
        """计算 top-N 标签的 pair similarity 并持久化。"""
        start = time.time()

        tag_data = self.db.get_all_tag_vectors()
        if not tag_data or len(tag_data) < 2:
            self._last_refresh = time.time()
            return

        # 只取频率最高的 max_tags 个
        # tag_data: [(id, name, vector), ...]
        if len(tag_data) > max_tags:
            # 按 DB 中 frequency 排序取 top
            try:
                rows = self.db.conn.execute(
                    "SELECT id FROM tags WHERE vector IS NOT NULL ORDER BY frequency DESC LIMIT ?",
                    (max_tags,),
                ).fetchall()
                top_ids = {r[0] for r in rows}
                tag_data = [(tid, name, vec) for tid, name, vec in tag_data if tid in top_ids]
            except Exception:
                tag_data = tag_data[:max_tags]

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
                    "INSERT INTO tag_pair_similarity (tag_id_a, tag_id_b, similarity, updated_at) VALUES (?, ?, ?, ?)",
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
