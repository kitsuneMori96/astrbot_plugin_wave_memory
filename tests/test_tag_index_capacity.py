from __future__ import annotations

from typing import Sequence

import numpy as np

from services.tag_index_capacity import hard_capacity, select_bounded_tag_vectors


def _blob(values: Sequence[float]) -> bytes:
    return np.asarray(values, dtype=np.float32).tobytes()


def test_select_bounded_tag_vectors_respects_hard_capacity_and_dimension():
    rows = [
        (1, _blob([1.0, 0.0])),
        (2, _blob([0.0])),  # wrong dimension
        (3, _blob([0.0, 1.0])),
        (4, _blob([0.5, 0.5])),
        (5, None),
    ]
    admitted = select_bounded_tag_vectors(rows, capacity=2, dimension=2)
    assert [item[0] for item in admitted] == [1, 3]


def test_hard_capacity_rejects_non_positive_values():
    assert hard_capacity(0, -3, "x", 40) == 40
    assert hard_capacity(None, default=7) == 7
