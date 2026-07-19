"""Wave Memory 残差金字塔 — 多层语义分解，提升复杂问题的召回精度"""

from __future__ import annotations

import numpy as np

from .vector_index import VectorIndex
from .database import WaveMemoryDB


class ResidualPyramid:
    """基于 Gram-Schmidt 正交化的残差金字塔分析。

    将查询向量逐层分解：每层找到最匹配的 Tag，
    然后从查询中"减去"已解释的语义，用残差继续搜索下一层。

    接收 db 参数，analyze() 按需取向量。
    """

    def __init__(self, tag_index: VectorIndex, db: WaveMemoryDB = None, max_levels: int = 3, top_k: int = 10, min_energy_ratio: float = 0.1):
        self.tag_index = tag_index
        self.db = db
        self.max_levels = max_levels
        self.top_k = top_k
        self.min_energy_ratio = min_energy_ratio

    def analyze(
        self,
        query_vector: np.ndarray,
        tag_vectors_by_id: dict[int, np.ndarray] = None,
        *,
        scope=None,
    ) -> dict:
        """执行残差金字塔分析。

        Args:
            query_vector: 原始查询向量 (float32)
            tag_vectors_by_id: {tag_id: vector} 映射。如果为 None，按需从 db 加载。
                （推荐 None，避免全量加载 8 万 tag 向量到内存）
        """
        query = query_vector.astype(np.float32)
        original_energy = float(np.dot(query, query))
        if original_energy < 1e-12:
            return {"levels": [], "all_tag_ids": [], "coverage": 0.0, "final_residual": query}

        current_residual = query.copy()
        levels = []
        all_tag_ids = []

        for level in range(self.max_levels):
            results = self.tag_index.search(current_residual, k=self.top_k)
            if not results:
                break

            # 按需取这一层候选 Catalog tag 的向量。正式 Scope 路径先把
            # Catalog ids 映射为 scoped ids，再用 scoped repository 取向量，
            # 但保留 Catalog id 作为索引输出，交由 QueryEngine 统一映射。
            cand_ids = [tid for tid, _ in results]
            catalog_to_scoped = {}
            if scope is not None and self.db is not None and callable(getattr(self.db, "list_scoped_catalog_links", None)):
                try:
                    links = self.db.list_scoped_catalog_links(scope, cand_ids) or []
                    catalog_to_scoped = {
                        int(item["catalog_id"]): int(item["scoped_tag_id"])
                        for item in links
                        if isinstance(item, dict)
                    }
                except Exception:
                    catalog_to_scoped = {}
                scoped_ids = [catalog_to_scoped[tid] for tid in cand_ids if tid in catalog_to_scoped]
                scoped_getter = getattr(self.db, "get_scoped_tag_vectors_by_ids", None)
                scoped_vecs = scoped_getter(scope, scoped_ids) if callable(scoped_getter) else {}
                level_vecs = {
                    catalog_id: scoped_vecs.get(scoped_id)
                    for catalog_id, scoped_id in catalog_to_scoped.items()
                    if scoped_vecs.get(scoped_id) is not None
                }
            elif tag_vectors_by_id is not None:
                level_vecs = {tid: tag_vectors_by_id[tid] for tid in cand_ids if tid in tag_vectors_by_id}
            elif self.db is not None:
                level_vecs = self.db.get_tag_vectors_by_ids(cand_ids)
            else:
                level_vecs = {}
                break

            level_tags = []
            projection_vectors = []

            for tag_id, distance in results:
                if tag_id not in level_vecs:
                    continue
                tag_vec = level_vecs[tag_id]
                similarity = 1.0 - distance

                if similarity < 0.05:
                    continue

                level_tags.append({
                    "tag_id": tag_id,
                    "similarity": similarity,
                    "level": level,
                })
                projection_vectors.append(tag_vec)
                all_tag_ids.append(tag_id)

            if not projection_vectors:
                break

            levels.append(level_tags)

            # Gram-Schmidt: 从残差中减去已解释的分量
            for pvec in projection_vectors:
                pvec_norm = pvec / (np.linalg.norm(pvec) + 1e-10)
                proj = np.dot(current_residual, pvec_norm) * pvec_norm
                current_residual = current_residual - proj * 0.5

            residual_energy = float(np.dot(current_residual, current_residual))
            if residual_energy / original_energy < self.min_energy_ratio:
                break

        final_energy = float(np.dot(current_residual, current_residual))
        coverage = 1.0 - (final_energy / original_energy)

        return {
            "levels": levels,
            "all_tag_ids": list(set(all_tag_ids)),
            "coverage": coverage,
            "final_residual": current_residual,
        }
