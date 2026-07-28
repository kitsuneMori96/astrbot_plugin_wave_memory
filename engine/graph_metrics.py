"""轻量图指标 — PageRank / 中心性 / 聚类系数 / 桥边 / 统计"""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from typing import Dict


def degree_centrality(forward: dict, backward: dict) -> Dict[int, float]:
    """出度 + 入度归一化中心性。"""
    deg: dict = defaultdict(int)
    all_nodes = set(forward) | set(backward)
    for src, neighbors in forward.items():
        deg[src] += len(neighbors)
    for tgt, neighbors in backward.items():
        deg[tgt] += len(neighbors)
    max_deg = max(deg.values()) if deg else 1
    return {n: deg[n] / max_deg for n in all_nodes}


def pagerank(
    forward: dict,
    backward: dict,
    damping: float = 0.85,
    max_iter: int = 20,
    tol: float = 1e-6,
) -> Dict[int, float]:
    """稀疏 PageRank — 基于 backward 入边迭代。"""
    nodes = set(forward) | set(backward)
    if not nodes:
        return {}
    n = len(nodes)
    pr = {node: 1.0 / n for node in nodes}
    out_deg = {src: max(len(nb), 1) for src, nb in forward.items()}
    for src in nodes:
        if src not in out_deg:
            out_deg[src] = 1

    for _ in range(max_iter):
        new_pr = {}
        total = 0.0
        for node in nodes:
            incoming = backward.get(node, {})
            s = sum(pr[src] / out_deg[src] for src in incoming)
            new_pr[node] = (1.0 - damping) / n + damping * s
            total += new_pr[node]
        diff = sum(abs(new_pr[n] - pr[n]) for n in nodes)
        pr = new_pr
        if diff < tol:
            break

    # 归一化
    max_pr = max(pr.values()) if pr else 1
    if max_pr > 0:
        pr = {k: v / max_pr for k, v in pr.items()}
    return pr


def clustering_coefficient(forward: dict, backward: dict) -> Dict[int, float]:
    """局部聚类系数（无向化处理）。"""
    adj: dict = defaultdict(set)
    all_nodes = set(forward) | set(backward)
    for src in forward:
        for tgt in forward[src]:
            adj[src].add(tgt)
            adj[tgt].add(src)

    cc: dict = {}
    for node in all_nodes:
        neighbors = adj.get(node, set())
        k = len(neighbors)
        if k < 2:
            cc[node] = 0.0
            continue
        triangles = sum(
            1 for u in neighbors for v in neighbors if u < v and v in adj.get(u, set())
        )
        cc[node] = triangles * 2.0 / (k * (k - 1))
    return cc


def global_avg_clustering(forward: dict, backward: dict) -> float:
    cc = clustering_coefficient(forward, backward)
    if not cc:
        return 0.0
    return sum(cc.values()) / len(cc)


def graph_stats(forward: dict, backward: dict) -> dict:
    """基础统计量。"""
    all_nodes = set(forward) | set(backward)
    v = len(all_nodes)
    e = sum(len(nb) for nb in forward.values())
    density = (e / (v * (v - 1))) if v > 1 else 0.0
    avg_deg = (e / v) if v else 0.0

    recip = 0
    for src, neighbors in forward.items():
        for tgt in neighbors:
            if forward.get(tgt, {}).get(src, 0) > 0:
                recip += 1
    reciprocity = (recip / e) if e else 0.0

    return {
        "node_count": v,
        "edge_count": e,
        "density": round(density, 6),
        "avg_degree": round(avg_deg, 4),
        "reciprocity": round(reciprocity, 4),
    }


def sampled_edge_betweenness(
    forward: dict,
    backward: dict,
    samples: int = 50,
) -> Dict[tuple, float]:
    """利用随机源节点 BFS 近似边介数。"""
    nodes = list(set(forward) | set(backward))
    if not nodes:
        return {}
    adj: dict = defaultdict(set)
    for src in forward:
        for tgt in forward[src]:
            adj[src].add(tgt)
            adj[tgt].add(src)
    edge_load: dict = defaultdict(float)
    sample_set = random.sample(nodes, min(samples, len(nodes)))
    for src in sample_set:
        visited = {src}
        queue = deque([(src, None)])
        while queue:
            cur, parent = queue.popleft()
            if parent is not None:
                edge_load[tuple(sorted((cur, parent)))] += 1.0
            for nb in adj.get(cur, set()):
                if nb not in visited:
                    visited.add(nb)
                    queue.append((nb, cur))
    max_load = max(edge_load.values()) if edge_load else 1.0
    return {k: v / max_load for k, v in edge_load.items()}
