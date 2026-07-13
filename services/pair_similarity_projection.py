"""Pure PairSimilarity projection computation for durable maintenance jobs."""

from __future__ import annotations

import time

import numpy as np


def compute_pair_similarity_projection(
    rows,
) -> tuple[list[tuple[int, int, float, float]], dict[tuple[int, int], float]]:
    """Build the persisted rows and read cache without performing any I/O."""
    if len(rows) < 2:
        return [], {}
    ids = [int(row[0]) for row in rows]
    vectors = np.asarray(
        [np.frombuffer(row[1], dtype=np.float32) for row in rows],
        dtype=np.float32,
    )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    vectors = vectors / np.where(norms < 1e-8, 1.0, norms)
    now = time.time()
    params = []
    cache = {}
    chunk_size = 500
    for start in range(0, len(ids), chunk_size):
        end = min(start + chunk_size, len(ids))
        block = vectors[start:end] @ vectors.T
        for local_i, global_i in enumerate(range(start, end)):
            for global_j in range(global_i + 1, len(ids)):
                similarity = float(block[local_i, global_j])
                if similarity > 0.1:
                    key = (ids[global_i], ids[global_j])
                    cache[key] = similarity
                    params.append((key[0], key[1], similarity, now))
    return params, cache
