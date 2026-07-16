"""Wave Memory 有向序位共现矩阵 — 语义增益调制 + 反向锚定 + 防抖修复"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from itertools import groupby
from typing import Optional

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - focused repository tests without AstrBot
    import logging
    logger = logging.getLogger(__name__)

from .database import WaveMemoryDB
from .db.scoped_tag_projection import effective_tag_rows
from .semantic_gain import bell_gain, SemanticGainConfig


def ordinal_potential(position: int, max_position: int) -> float:
    """计算序位势能 Φ ∈ [0.5, 0.9]。"""
    if position <= 0:
        return 0.7
    if max_position <= 1:
        return 0.7
    return 0.9 - 0.4 * (position - 1) / (max_position - 1)


class DirectedCooccurrence:
    """有向序位共现矩阵 + 语义增益调制 + 反向锚定。

    与旧 CooccurrenceMatrix 接口兼容。
    """

    def __init__(self, db: WaveMemoryDB, pair_sim_service=None, residual_map: dict = None, semantic_gain_config: SemanticGainConfig = None):
        self.db = db
        self.pair_sim_service = pair_sim_service
        self.residual_map = residual_map or {}
        self.semantic_gain_config = semantic_gain_config or SemanticGainConfig()
        # {source_id: {target_id: directed_weight}}
        self.forward: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        # 反向索引
        self.backward: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        self._tag_count = 0

    def rebuild(self):
        """从 memory_tags 构建有向共现矩阵，加入语义增益调制。"""
        new_forward: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        new_backward: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))

        has_scoped = self.db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scoped_memory_tags'"
        ).fetchone()
        if has_scoped:
            effective_rows = effective_tag_rows(self.db.conn)
            rows = [
                (
                    row["bot_id"],
                    row["session_id"],
                    row["visibility"],
                    int(row["memory_id"]),
                    int(row["tag_id"]),
                    int(row["position"]),
                )
                for row in effective_rows
                if row["tag_id"] is not None
            ]
            rows.sort(key=lambda row: (row[0], row[1], row[2], row[3], row[5], row[4]))
        else:
            rows = [
                (None, None, None, int(row[0]), int(row[1]), int(row[2]))
                for row in self.db.conn.execute(
                    "SELECT memory_id, tag_id, position FROM memory_tags ORDER BY memory_id, position"
                ).fetchall()
            ]

        if not rows:
            self.forward = new_forward
            self.backward = new_backward
            self._tag_count = 0
            logger.info("[WaveMemory] DirectedCooccurrence: no data")
            return

        # 按完整 Scope + memory_id 分组，避免不同 Scope 的同号对象混合。
        for memory_key, group in groupby(rows, key=lambda r: (r[0], r[1], r[2], r[3])):
            del memory_key
            tags = [(r[4], r[5]) for r in group]
            if len(tags) < 2:
                continue

            max_pos = max(p for _, p in tags) if tags else 1

            for i, (src_id, src_pos) in enumerate(tags):
                src_phi = ordinal_potential(src_pos, max_pos)
                for j, (tgt_id, tgt_pos) in enumerate(tags):
                    if i == j or src_id == tgt_id:
                        continue
                    tgt_phi = ordinal_potential(tgt_pos, max_pos)
                    weight = src_phi * tgt_phi

                    # 语义增益调制：用 pair_sim 调节边权重
                    if self.pair_sim_service:
                        sim = self.pair_sim_service.get_similarity(src_id, tgt_id)
                        gain = bell_gain(sim, self.semantic_gain_config)
                        weight *= gain

                    # 反向锚定：高残差节点作为 target 时加权
                    tgt_residual = self.residual_map.get(tgt_id, 0.5)
                    weight *= (0.7 + 0.6 * tgt_residual)

                    new_forward[src_id][tgt_id] += weight
                    new_backward[tgt_id][src_id] += weight

        # 归一化
        max_w = 0.0
        for neighbors in new_forward.values():
            for w in neighbors.values():
                if w > max_w:
                    max_w = w

        if max_w > 0:
            for src in new_forward:
                for tgt in new_forward[src]:
                    new_forward[src][tgt] /= max_w
            for tgt in new_backward:
                for src in new_backward[tgt]:
                    new_backward[tgt][src] /= max_w

        # 剪枝
        for src in list(new_forward.keys()):
            new_forward[src] = {tgt: w for tgt, w in new_forward[src].items() if w >= 0.01}
            if not new_forward[src]:
                del new_forward[src]

        for tgt in list(new_backward.keys()):
            new_backward[tgt] = {src: w for src, w in new_backward[tgt].items() if w >= 0.01}
            if not new_backward[tgt]:
                del new_backward[tgt]

        # 原子切换
        self.forward = new_forward
        self.backward = new_backward
        if has_scoped:
            count_row = self.db.conn.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()
            self._tag_count = int(count_row[0]) if count_row else 0
        else:
            self._tag_count = self.db.get_tag_count()

        logger.info(
            f"[WaveMemory] DirectedCooccurrence rebuilt: "
            f"{len(self.forward)} nodes, {sum(len(v) for v in self.forward.values())} directed edges"
        )

    def get_neighbors(self, tag_id: int, max_neighbors: int = 20) -> list[tuple[int, float]]:
        """获取某个 Tag 的有向出边邻居，按权重降序。"""
        neighbors = self.forward.get(tag_id, {})
        sorted_n = sorted(neighbors.items(), key=lambda x: x[1], reverse=True)
        return sorted_n[:max_neighbors]

    def get_incoming(self, tag_id: int, max_neighbors: int = 20) -> list[tuple[int, float]]:
        """获取指向该 Tag 的入边邻居。"""
        neighbors = self.backward.get(tag_id, {})
        sorted_n = sorted(neighbors.items(), key=lambda x: x[1], reverse=True)
        return sorted_n[:max_neighbors]

    # ─── 社区检测 ───

    def detect_communities(self, min_community_size: int = 3) -> dict[int, list[int]]:
        adj: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        for src, neighbors in self.forward.items():
            for tgt, w in neighbors.items():
                adj[src][tgt] += w
                adj[tgt][src] += w

        if not adj:
            return {}

        labels: dict[int, int] = {node: node for node in adj}
        nodes = list(adj.keys())

        import random
        for _ in range(20):
            random.shuffle(nodes)
            changed = False
            for node in nodes:
                if not adj[node]:
                    continue
                votes: dict[int, float] = defaultdict(float)
                for neighbor, weight in adj[node].items():
                    votes[labels[neighbor]] += weight
                if votes:
                    best_label = max(votes, key=lambda k: votes[k])
                    if labels[node] != best_label:
                        labels[node] = best_label
                        changed = True
            if not changed:
                break

        communities: dict[int, list[int]] = defaultdict(list)
        for node, label in labels.items():
            communities[label].append(node)

        result: dict[int, list[int]] = {}
        for i, (_, members) in enumerate(
            sorted(communities.items(), key=lambda x: len(x[1]), reverse=True)
        ):
            if len(members) < min_community_size:
                continue
            result[i] = members

        return result

    def get_galaxy_data(self, max_nodes: int = 300, max_edges: int = 800) -> dict:
        """生成全局星图数据。"""
        communities = self.detect_communities(min_community_size=5)
        if not communities:
            return {"nodes": [], "edges": [], "communities": []}

        degree: dict[int, int] = defaultdict(int)
        for src, neighbors in self.forward.items():
            degree[src] += len(neighbors)
        for tgt, neighbors in self.backward.items():
            degree[tgt] += len(neighbors)

        selected_nodes: set[int] = set()
        community_meta: list[dict] = []
        nodes_per_community = max(3, max_nodes // max(len(communities), 1))

        for cid, members in communities.items():
            sorted_members = sorted(members, key=lambda n: degree.get(n, 0), reverse=True)
            top_members = sorted_members[:nodes_per_community]
            selected_nodes.update(top_members)
            community_meta.append({"id": cid, "size": len(members), "top_nodes": top_members[:3]})
            if len(selected_nodes) >= max_nodes:
                break

        edges: list[dict] = []
        for src in selected_nodes:
            if src not in self.forward:
                continue
            for tgt, weight in self.forward[src].items():
                if tgt in selected_nodes and weight >= 0.05:
                    edges.append({"source": src, "target": tgt, "weight": round(weight, 3)})
                    if len(edges) >= max_edges:
                        break
            if len(edges) >= max_edges:
                break

        node_info: dict[int, dict] = {}
        if selected_nodes:
            placeholders = ",".join("?" * len(selected_nodes))
            has_scoped = self.db.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scoped_tags'"
            ).fetchone()
            tag_table = "scoped_tags" if has_scoped else "tags"
            rows = self.db.conn.execute(
                f"SELECT id, name, tag_type FROM {tag_table} WHERE id IN ({placeholders})",
                list(selected_nodes),
            ).fetchall()
            for r in rows:
                node_info[r[0]] = {"id": r[0], "name": r[1], "type": r[2], "degree": degree.get(r[0], 0)}

        node_community: dict[int, int] = {}
        for cid, members in communities.items():
            for m in members:
                if m in selected_nodes:
                    node_community[m] = cid

        nodes = []
        for nid in selected_nodes:
            if nid in node_info:
                info = node_info[nid]
                info["community"] = node_community.get(nid, -1)
                nodes.append(info)

        return {"nodes": nodes, "edges": edges, "communities": community_meta}

    @property
    def node_count(self) -> int:
        return len(self.forward)

    @property
    def edge_count(self) -> int:
        return sum(len(v) for v in self.forward.values())

    def needs_rebuild(self, threshold_pct: float = 0.05) -> bool:
        """判断是否需要重建（阈值改为 0.05）。"""
        has_scoped = self.db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scoped_tags'"
        ).fetchone()
        if has_scoped:
            row = self.db.conn.execute("SELECT COUNT(*) FROM scoped_tags").fetchone()
            current_count = int(row[0]) if row else 0
        else:
            current_count = self.db.get_tag_count()
        if self._tag_count == 0:
            return current_count > 10
        change = abs(current_count - self._tag_count) / self._tag_count
        return change >= threshold_pct


class CooccurrenceScheduler:
    """防抖重建调度器 — 修复：改成满阈值+过冷却期才触发。

    累积 Tag 变更超过阈值后，检查冷却期是否已过，再触发重建。
    """

    def __init__(
        self,
        cooccurrence: DirectedCooccurrence,
        threshold_pct: float = 0.05,
        cooldown_sec: float = 300,
        on_rebuild_complete=None,
    ):
        self.cooccurrence = cooccurrence
        self.threshold_pct = threshold_pct
        self.cooldown_sec = cooldown_sec
        self.on_rebuild_complete = on_rebuild_complete
        self._accumulated_changes = 0
        self._change_generation = 0
        self._last_rebuild_ts: float = 0
        self._is_rebuilding = False
        self._scheduled_task = None

    def notify_tag_change(self, count: int = 1):
        """Accumulate Tag deltas and schedule at most one background rebuild."""
        normalized_count = max(int(count), 0)
        self._accumulated_changes += normalized_count
        self._change_generation += normalized_count
        total = self.cooccurrence.node_count or 1
        if self._accumulated_changes / total >= self.threshold_pct:
            now = time.time()
            if now - self._last_rebuild_ts >= self.cooldown_sec:
                self._schedule_rebuild()

    def _schedule_rebuild(self):
        """Schedule one rebuild; repeated notifications only advance generation."""
        if self._is_rebuilding:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._is_rebuilding = True
        self._scheduled_task = loop.create_task(self._do_rebuild())

    async def _do_rebuild(self):
        """Rebuild off the AstrBot event loop and consume a generation snapshot."""
        generation = self._change_generation
        try:
            logger.info(
                "[WaveMemory] CooccurrenceScheduler: starting rebuild generation=%s pending_changes=%s",
                generation,
                self._accumulated_changes,
            )
            new_matrix = DirectedCooccurrence(
                self.cooccurrence.db,
                pair_sim_service=self.cooccurrence.pair_sim_service,
                residual_map=self.cooccurrence.residual_map,
                semantic_gain_config=self.cooccurrence.semantic_gain_config,
            )
            await asyncio.to_thread(new_matrix.rebuild)
            self.cooccurrence.forward = new_matrix.forward
            self.cooccurrence.backward = new_matrix.backward
            self.cooccurrence._tag_count = new_matrix._tag_count
            consumed = min(generation, self._change_generation)
            self._accumulated_changes = max(0, self._change_generation - consumed)
            self._last_rebuild_ts = time.time()
            logger.info(
                "[WaveMemory] CooccurrenceScheduler: rebuild complete generation=%s remaining_changes=%s",
                generation,
                self._accumulated_changes,
            )

            if self.on_rebuild_complete:
                await self.on_rebuild_complete()

        except Exception as e:
            logger.error(
                "[WaveMemory] CooccurrenceScheduler rebuild error type=%s error=%r",
                type(e).__name__,
                e,
            )
        finally:
            self._is_rebuilding = False
            self._scheduled_task = None
