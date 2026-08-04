"""Pure PairSimilarity projection computation for durable maintenance jobs.

This deliberately stores only a sparse Top-K neighborhood per tag.  The previous
O(n^2) upper-triangle dump created ~2M rows for only 2,000 tags and forced the
maintenance path to materialize multi-million-entry Python lists on every rebuild.
"""

from __future__ import annotations

import time
from typing import Iterable

import numpy as np

# Defaults kept intentionally conservative so a single rebuild stays bounded.
DEFAULT_MAX_TAGS = 800
DEFAULT_TOP_K = 16
DEFAULT_MIN_SIMILARITY = 0.20
DEFAULT_CHUNK_SIZE = 128
ABSOLUTE_MAX_TAGS = 2000
ABSOLUTE_TOP_K = 64


def _decode_vectors(rows: Iterable) -> tuple[list[int], np.ndarray]:
    ids: list[int] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        try:
            tag_id = int(row[0])
            blob = row[1]
            if blob is None:
                continue
            if isinstance(blob, memoryview):
                blob = blob.tobytes()
            vector = np.frombuffer(blob, dtype=np.float32)
            if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
                continue
        except (TypeError, ValueError, IndexError):
            continue
        ids.append(tag_id)
        vectors.append(np.asarray(vector, dtype=np.float32).copy())
    if not ids:
        return [], np.empty((0, 0), dtype=np.float32)
    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms < 1e-8, 1.0, norms)
    return ids, matrix


def compute_pair_similarity_projection(
    rows,
    *,
    top_k: int = DEFAULT_TOP_K,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[list[tuple[int, int, float, float]], dict[tuple[int, int], float]]:
    """Build a sparse Top-K pair projection without performing any I/O.

    For each tag, only the strongest ``top_k`` neighbors above
    ``min_similarity`` are retained.  Undirected edges are stored once with
    ``tag_id_a < tag_id_b`` so readers can keep using a single ordered key.
    """
    ids, vectors = _decode_vectors(rows)
    n = len(ids)
    if n < 2:
        return [], {}

    try:
        k = max(1, min(int(top_k), ABSOLUTE_TOP_K, n - 1))
    except (TypeError, ValueError):
        k = min(DEFAULT_TOP_K, n - 1)
    try:
        threshold = float(min_similarity)
    except (TypeError, ValueError):
        threshold = DEFAULT_MIN_SIMILARITY
    if not np.isfinite(threshold):
        threshold = DEFAULT_MIN_SIMILARITY
    try:
        block = max(1, min(int(chunk_size), n))
    except (TypeError, ValueError):
        block = DEFAULT_CHUNK_SIZE

    now = time.time()
    # Collect best undirected edges; later collisions keep the higher score.
    best: dict[tuple[int, int], float] = {}
    for start in range(0, n, block):
        end = min(start + block, n)
        sims = vectors[start:end] @ vectors.T
        for local_i, global_i in enumerate(range(start, end)):
            row = sims[local_i]
            # Mask self-similarity so argpartition cannot select the tag itself.
            row = np.array(row, copy=True)
            row[global_i] = -1.0
            if n - 1 <= k:
                candidate_idx = np.argsort(row)[::-1]
            else:
                part = np.argpartition(row, -k)[-k:]
                candidate_idx = part[np.argsort(row[part])[::-1]]
            for j in candidate_idx:
                similarity = float(row[int(j)])
                if similarity < threshold:
                    break
                a = ids[global_i]
                b = ids[int(j)]
                if a == b:
                    continue
                key = (a, b) if a < b else (b, a)
                previous = best.get(key)
                if previous is None or similarity > previous:
                    best[key] = similarity

    params = [
        (key[0], key[1], similarity, now)
        for key, similarity in sorted(best.items(), key=lambda item: (item[0][0], item[0][1]))
    ]
    cache = {(a, b): sim for a, b, sim, _ in params}
    return params, cache


__all__ = [
    "ABSOLUTE_MAX_TAGS",
    "ABSOLUTE_TOP_K",
    "DEFAULT_MAX_TAGS",
    "DEFAULT_MIN_SIMILARITY",
    "DEFAULT_TOP_K",
    "compute_pair_similarity_projection",
]
