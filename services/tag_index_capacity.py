"""Bounded admission helpers for Tag HNSW rebuilds.

Both formal Catalog and legacy tag indexes must respect their configured
``max_vectors`` as a hard ceiling.  Excess rows remain in SQLite; they are simply
not admitted to the resident process index.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def select_bounded_tag_vectors(
    rows: Iterable[Any],
    *,
    capacity: int,
    dimension: int,
    vector_bytes_expected: int | None = None,
) -> list[tuple[int, Any]]:
    """Return up to ``capacity`` valid (id, vector_bytes) pairs in input order.

    Callers are expected to pre-order rows by their preferred ranking (frequency,
    recency, etc.).  This helper only enforces dimension validity and the hard
    count ceiling so rebuild code cannot silently reintroduce auto-expansion.
    """
    try:
        limit = max(0, int(capacity))
    except (TypeError, ValueError):
        limit = 0
    try:
        dim = int(dimension)
    except (TypeError, ValueError):
        return []
    if limit <= 0 or dim <= 0:
        return []

    expected = (
        int(vector_bytes_expected)
        if vector_bytes_expected is not None
        else dim * 4  # float32
    )
    admitted: list[tuple[int, Any]] = []
    for row in rows:
        if len(admitted) >= limit:
            break
        try:
            tag_id = int(row[0])
            blob = row[1]
        except (TypeError, ValueError, IndexError):
            continue
        if blob is None:
            continue
        try:
            size = len(blob)
        except TypeError:
            continue
        if size != expected:
            continue
        admitted.append((tag_id, blob))
    return admitted


def hard_capacity(*values: int, default: int = 1) -> int:
    """Return a positive hard capacity from configuration-like integers."""
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return max(1, int(default))


__all__ = ["hard_capacity", "select_bounded_tag_vectors"]
