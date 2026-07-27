"""Wave Memory 查询引擎 V2 — tag 向量按需加载（删全量缓存）"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from astrbot.api import logger

from .database import WaveMemoryDB
from .vector_index import VectorIndex
from .embedding import EmbeddingService
from .directed_cooccurrence import DirectedCooccurrence
from .context_segmenter import ContextSegmenter
from .spike_routing import SpikeRouter
from .residual_pyramid import ResidualPyramid
from .epa import EPAModule
from .geodesic_rerank import GeodesicReranker


class QueryEngine:
    """记忆查询管线 V2：EPA → 残差金字塔 → 脉冲传播 → 向量融合 → 检索 → 测地线重排。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: EmbeddingService,
        config: dict,
        tag_index: Optional[VectorIndex] = None,
        cooccurrence: Optional[DirectedCooccurrence] = None,
        spike_router: Optional[SpikeRouter] = None,
        residual_pyramid: Optional[ResidualPyramid] = None,
        epa: Optional[EPAModule] = None,
        geodesic: Optional[GeodesicReranker] = None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.config = config
        self.tag_index = tag_index
        self.cooccurrence = cooccurrence
        self.spike_router = spike_router
        self.residual_pyramid = residual_pyramid
        self.epa = epa
        self.geodesic = geodesic

        # 配置参数（对齐默认值）
        self.min_similarity = float(config.get("min_similarity", "0.35"))
        self.enable_spike = config.get("enable_spike_routing", True)
        self.enable_pyramid = config.get("enable_residual_pyramid", True)
        self.enable_epa = config.get("enable_epa", True)
        self.enable_geodesic = config.get("enable_geodesic_rerank", True)

    async def query(
        self,
        text: str,
        group_id: Optional[str] = None,
        top_k: int = 5,
        exclude_sources: Optional[list[str]] = None,
        source_filter: Optional[str | list[str]] = None,
    ) -> list[dict]:
        """执行完整的浪潮查询管线。
        
        Args:
            exclude_sources: 排除特定 source 类型的记忆（如 ["bzz_experience"]）
            source_filter: 只保留特定 source 类型的记忆，支持单个或列表
        """
        start = time.time()

        query_vec = await self.embedding.get_embedding(text)
        if query_vec is None:
            return []

        embed_ms = (time.time() - start) * 1000

        search_vec, energy_field = self._wave_boost(query_vec)
        # 当只搜特定 source 时，需要更多候选（因为大部分会被过滤掉）
        candidates_k = top_k * 20 if source_filter else top_k * 3
        results = self.memory_index.search(search_vec, k=candidates_k)

        if not results:
            return []

        memory_ids = [r[0] for r in results]
        distances = {r[0]: r[1] for r in results}
        memories = self.db.get_memories_by_ids(memory_ids)

        # 只保留特定 source（优先于 exclude_sources）
        if source_filter:
            if isinstance(source_filter, str):
                source_filter = [source_filter]
            memories = [m for m in memories if m.get("source", "live") in source_filter]
        # 按 source 字段过滤（用于多 bot 场景：羽书排除白真真经历等）
        elif exclude_sources:
            memories = [m for m in memories if m.get("source", "live") not in exclude_sources]

        cross_group_enabled = self.config.get("cross_group_enabled", True)
        if not cross_group_enabled and group_id:
            memories = [m for m in memories if m.get("group_id", "") == group_id]
        for mem in memories:
            mem["_is_cross_group"] = (mem.get("group_id", "") != group_id) if group_id else False

        for mem in memories:
            dist = distances.get(mem["id"], 1.0)
            mem["similarity"] = 1.0 - dist

            ts = mem.get("timestamp", None)
            if isinstance(ts, str):
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    ts = time.time()
            elif ts is None:
                ts = time.time()
            days_old = (time.time() - ts) / 86400.0
            time_decay = 0.997 ** max(0, days_old)

            access_count = mem.get("access_count", 0) or 0
            import math
            access_boost = 1.0 + math.log2(1 + access_count) * 0.15

            mem["score"] = mem["similarity"] * mem.get("importance", 1.0) * time_decay * access_boost

        if self.enable_geodesic and self.geodesic and energy_field:
            candidates_for_rerank = [{"id": m["id"], "score": m["score"]} for m in memories]
            reranked = self.geodesic.rerank(candidates_for_rerank, energy_field)
            rerank_scores = {c["id"]: c["score"] for c in reranked}
            for mem in memories:
                if mem["id"] in rerank_scores:
                    mem["score"] = rerank_scores[mem["id"]]

        # 过滤：只看相似度 >= min_similarity
        memories = [m for m in memories if m["similarity"] >= self.min_similarity]
        memories.sort(key=lambda m: m["score"], reverse=True)
        memories = memories[:top_k]

        if memories:
            self.db.touch_memories([m["id"] for m in memories])

        total_ms = (time.time() - start) * 1000
        logger.debug(
            f"[WaveMemory] Query done: {len(memories)} results, "
            f"embed={embed_ms:.0f}ms, total={total_ms:.0f}ms"
        )
        return memories

    def _wave_boost(self, query_vec: np.ndarray) -> tuple[np.ndarray, dict]:
        """VCP TagMemo 浪潮增强。"""
        energy_field = {}

        if not self.tag_index or self.tag_index.count < 10:
            return query_vec, energy_field

        logic_depth = 0.5
        entropy = 0.5
        if self.enable_epa and self.epa and self.epa.initialized:
            try:
                epa_result = self.epa.analyze(query_vec)
                logic_depth = epa_result.get("logic_depth", 0.5)
                entropy = epa_result.get("entropy", 0.5)
            except Exception:
                logic_depth = 0.5
                entropy = 0.5

        matched_tags = []

        if self.enable_pyramid and self.residual_pyramid:
            # 残差金字塔内部按需从 db 取候选 tag 向量
            pyramid_result = self.residual_pyramid.analyze(query_vec, None)
            for level_tags in pyramid_result.get("levels", []):
                for tag_info in level_tags:
                    tid = tag_info.get("tag_id")
                    sim = tag_info.get("similarity", 0)
                    if tid and sim > 0.1:
                        matched_tags.append((tid, sim))
        else:
            tag_results = self.tag_index.search(query_vec, k=10)
            for tid, dist in tag_results:
                sim = 1.0 - dist
                if sim > 0.2:
                    matched_tags.append((tid, sim))

        if not matched_tags:
            return query_vec, energy_field

        if self.enable_spike and self.spike_router and self.cooccurrence and self.cooccurrence.node_count > 0:
            seed_tags = [{"tag_id": tid, "weight": w} for tid, w in matched_tags[:10]]
            epa_for_spike = {"logic_depth": logic_depth, "entropy": entropy}
            spike_result = self.spike_router.propagate(seed_tags, epa_result=epa_for_spike)
            energy_field = spike_result.get("energy_field", {})

            for activated in spike_result.get("activated_tags", []):
                tid = activated["tag_id"]
                energy = activated["energy"]
                if activated.get("is_emergent") and energy > 0.1:
                    matched_tags.append((tid, energy * 0.5))

        # 向量融合
        base_boost = 0.3
        dynamic_factor = logic_depth * (1.0 / (1.0 + entropy * 0.5))
        alpha = min(0.6, base_boost * max(0.5, min(2.0, dynamic_factor)))

        # 去重（保留最高权重）
        tag_weights = {}
        for tid, w in matched_tags:
            if tid not in tag_weights or w > tag_weights[tid]:
                tag_weights[tid] = w

        # 按需只取命中 tag 的向量（避免全量加载 8 万 tag）
        tag_vecs = self.db.get_tag_vectors_by_ids(list(tag_weights.keys()))

        context_vec = np.zeros_like(query_vec)
        total_weight = 0.0

        for tid, weight in tag_weights.items():
            if tid in tag_vecs:
                context_vec += tag_vecs[tid] * weight
                total_weight += weight

        if total_weight > 0:
            context_vec /= total_weight
            norm = np.linalg.norm(context_vec)
            if norm > 1e-10:
                context_vec /= norm

            fused = (1 - alpha) * query_vec + alpha * context_vec
            fused_norm = np.linalg.norm(fused)
            if fused_norm > 1e-10:
                fused /= fused_norm
            return fused.astype(np.float32), energy_field

        return query_vec, energy_field

    async def shotgun_query(
        self,
        text: str,
        context_messages: list[str] = None,
        group_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[dict]:
        """多路霰弹枪检索。"""
        start = time.time()

        query_vec = await self.embedding.get_embedding(text)
        if query_vec is None:
            return []

        search_vec, energy_field = self._wave_boost(query_vec)
        main_results = self.memory_index.search(search_vec, k=top_k * 3)

        segment_results = []
        if context_messages:
            segmenter = ContextSegmenter(
                similarity_threshold=float(self.config.get("shotgun_similarity_threshold", 0.70)),
                max_segments=int(self.config.get("shotgun_max_segments", 3)),
            )
            ctx_vecs = await self.embedding.get_embeddings(context_messages)
            if ctx_vecs:
                segment_vecs = segmenter.segment(ctx_vecs)
                for seg_vec in segment_vecs:
                    seg_results = self.memory_index.search(seg_vec, k=top_k * 2)
                    segment_results.extend(seg_results)

        all_candidates = {}
        for mem_id, dist in main_results:
            all_candidates[mem_id] = min(all_candidates.get(mem_id, 999), dist)
        for mem_id, dist in segment_results:
            all_candidates[mem_id] = min(all_candidates.get(mem_id, 999), dist)

        if not all_candidates:
            return []

        memory_ids = list(all_candidates.keys())
        memories = self.db.get_memories_by_ids(memory_ids)

        for mem in memories:
            mem["_is_cross_group"] = (mem.get("group_id", "") != group_id) if group_id else False

        for mem in memories:
            dist = all_candidates.get(mem["id"], 1.0)
            mem["similarity"] = 1.0 - dist
            mem["score"] = mem["similarity"] * mem.get("importance", 1.0)

        if self.enable_geodesic and self.geodesic and energy_field:
            candidates_for_rerank = [{"id": m["id"], "score": m["score"]} for m in memories]
            reranked = self.geodesic.rerank(candidates_for_rerank, energy_field)
            rerank_scores = {c["id"]: c["score"] for c in reranked}
            for mem in memories:
                if mem["id"] in rerank_scores:
                    mem["score"] = rerank_scores[mem["id"]]

        memories = [m for m in memories if m["similarity"] >= self.min_similarity]
        if len(memories) > top_k:
            memories = self._svd_dedup(memories, query_vec, top_k)
        else:
            memories.sort(key=lambda m: m["score"], reverse=True)
            memories = memories[:top_k]

        if memories:
            self.db.touch_memories([m["id"] for m in memories])

        total_ms = (time.time() - start) * 1000
        logger.debug(
            f"[WaveMemory] Shotgun query done: {len(memories)} results, "
            f"candidates={len(all_candidates)}, total={total_ms:.0f}ms"
        )
        return memories

    def _svd_dedup(self, memories: list[dict], query_vec: np.ndarray, top_k: int) -> list[dict]:
        """SVD 主题去重。"""
        mem_ids = [m["id"] for m in memories]
        mem_vectors = self.db.get_memory_vectors(mem_ids)

        if not mem_vectors:
            memories.sort(key=lambda m: m["score"], reverse=True)
            return memories[:top_k]

        memories.sort(key=lambda m: m["score"], reverse=True)
        selected = []
        selected_vecs = []

        for mem in memories:
            if len(selected) >= top_k:
                break
            vec = mem_vectors.get(mem["id"])
            if vec is None:
                selected.append(mem)
                continue
            if selected_vecs:
                basis = np.vstack(selected_vecs)
                proj_coeffs = basis @ vec
                projection = proj_coeffs @ basis
                residual = vec - projection
                residual_norm = np.linalg.norm(residual)
                if residual_norm < 0.3:
                    continue
            selected.append(mem)
            norm = np.linalg.norm(vec)
            if norm > 1e-8:
                selected_vecs.append(vec / norm)

        return selected

    async def query_by_person(self, qq_id: str, topic: str = None, group_id: str = None, top_k: int = 8) -> list[dict]:
        """按人查询记忆。"""
        if topic:
            memories = self.db.get_memories_by_person(qq_id, limit=top_k * 5)
            if not memories:
                return []
            topic_vec = await self.embedding.get_embedding(topic)
            if topic_vec is None:
                return memories[:top_k]
            search_vec, _ = self._wave_boost(topic_vec)
            memory_ids = [m["id"] for m in memories]
            mem_vectors = self.db.get_memory_vectors(memory_ids)
            for mem in memories:
                vec = mem_vectors.get(mem["id"])
                if vec is not None:
                    sim = float(np.dot(search_vec, vec) / (np.linalg.norm(search_vec) * np.linalg.norm(vec) + 1e-10))
                    mem["score"] = max(0, sim) * mem.get("importance", 1.0)
                else:
                    mem["score"] = 0.0
            memories.sort(key=lambda m: m["score"], reverse=True)
            return [m for m in memories[:top_k] if m["score"] > 0.1]
        else:
            memories = self.db.get_memories_by_person(qq_id, role="sender", limit=top_k)
            for m in memories:
                m["score"] = m.get("importance", 1.0)
            return memories

    def format_injection(self, memories: list[dict], template: str = "", current_group_id: str = "") -> str:
        """将记忆列表格式化为注入文本，按 source 类型分段。"""
        if not memories:
            return ""
        if not template:
            template = "[记忆] {sender}({time}): {content}"

        # 按 source 分组
        your_memories = []  # bzz_experience — 白真真第一人称经历
        world_knowledge = []  # book_lore — 书设常识
        chat_memories = []  # live 及其他 — 群聊记忆

        for mem in memories:
            source = mem.get("source", "live")
            if source == "bzz_experience":
                your_memories.append(mem)
            elif source == "book_lore":
                world_knowledge.append(mem)
            else:
                chat_memories.append(mem)

        sections = []

        # 第一人称经历：简洁格式，不加 sender 前缀
        if your_memories:
            lines = ["<your_memory>"]
            for mem in your_memories:
                content = mem.get("content", "")
                lines.append(content)
            lines.append("</your_memory>")
            sections.append("\n".join(lines))

        # 群聊记忆：标准格式
        if chat_memories:
            lines = ["<wave_memory>"]
            for mem in chat_memories:
                sender = mem.get("sender_name") or mem.get("sender_id") or "unknown"
                ts = time.strftime("%m-%d %H:%M", time.localtime(mem["timestamp"]))
                content = mem.get("content", "")
                score = mem.get("score", 0)
                group_id = mem.get("group_id", "")
                group_tag = ""
                if mem.get("_is_cross_group") and group_id:
                    group_tag = f"[群{group_id}] "
                line = template.replace("{sender}", sender).replace("{time}", ts).replace("{content}", content)
                lines.append(f"{group_tag}{line} (relevance: {score:.2f})")
            lines.append("</wave_memory>")
            sections.append("\n".join(lines))

        # 书设常识：简洁格式
        if world_knowledge:
            lines = ["<world_knowledge>"]
            for mem in world_knowledge:
                content = mem.get("content", "")
                lines.append(content)
            lines.append("</world_knowledge>")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)
