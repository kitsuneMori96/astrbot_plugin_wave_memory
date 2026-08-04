"""Resident bound on the directed cooccurrence neighbour graph.

The graph is rebuilt in full and kept resident, so an unbounded neighbour width
directly raises steady memory.  These tests pin the bound and the forward/backward
consistency that the bound must preserve.
"""

from __future__ import annotations

import sqlite3
import unittest

from engine.directed_cooccurrence import (
    DEFAULT_MAX_NEIGHBORS_PER_TAG,
    DirectedCooccurrence,
)


class _Db:
    """Minimal DB facade exposing only what rebuild() reads."""

    def __init__(self, rows: list[tuple[int, int, int]], tag_count: int):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE memory_tags (memory_id INTEGER, tag_id INTEGER, position INTEGER);
            """
        )
        self.conn.executemany("INSERT INTO memory_tags VALUES (?, ?, ?)", rows)
        self.conn.commit()
        self._tag_count = tag_count

    def get_tag_count(self) -> int:
        return self._tag_count


def _wide_memory_rows(tag_count: int) -> list[tuple[int, int, int]]:
    """One memory carrying many tags produces a dense tag cross-product."""
    return [(1, tag_id, position) for position, tag_id in enumerate(range(1, tag_count + 1), 1)]


class CooccurrenceNeighborBoundTest(unittest.TestCase):
    def test_default_bound_is_applied_to_a_wide_tag_memory(self):
        tag_count = 40
        db = _Db(_wide_memory_rows(tag_count), tag_count)
        matrix = DirectedCooccurrence(db, max_neighbors_per_tag=8)

        matrix.rebuild()

        self.assertTrue(matrix.forward)
        for source, neighbors in matrix.forward.items():
            self.assertLessEqual(len(neighbors), 8, source)

    def test_backward_never_references_a_pruned_forward_edge(self):
        tag_count = 30
        db = _Db(_wide_memory_rows(tag_count), tag_count)
        matrix = DirectedCooccurrence(db, max_neighbors_per_tag=5)

        matrix.rebuild()

        for target, inbound in matrix.backward.items():
            for source, weight in inbound.items():
                self.assertIn(target, matrix.forward.get(source, {}))
                self.assertAlmostEqual(matrix.forward[source][target], weight)

    def test_bound_keeps_the_strongest_edges(self):
        tag_count = 24
        db = _Db(_wide_memory_rows(tag_count), tag_count)
        unbounded = DirectedCooccurrence(db, max_neighbors_per_tag=tag_count)
        unbounded.rebuild()

        bounded = DirectedCooccurrence(db, max_neighbors_per_tag=4)
        bounded.rebuild()

        for source, neighbors in bounded.forward.items():
            full = unbounded.forward[source]
            strongest = sorted(full.items(), key=lambda item: (-item[1], item[0]))[:4]
            self.assertEqual(sorted(neighbors), sorted(key for key, _ in strongest))

    def test_nonpositive_or_malformed_bound_falls_back_to_default(self):
        db = _Db(_wide_memory_rows(4), 4)

        self.assertEqual(
            DirectedCooccurrence(db, max_neighbors_per_tag=0).max_neighbors_per_tag,
            DEFAULT_MAX_NEIGHBORS_PER_TAG,
        )
        self.assertEqual(
            DirectedCooccurrence(db, max_neighbors_per_tag=-5).max_neighbors_per_tag,
            DEFAULT_MAX_NEIGHBORS_PER_TAG,
        )
        self.assertEqual(
            DirectedCooccurrence(db, max_neighbors_per_tag="nope").max_neighbors_per_tag,
            DEFAULT_MAX_NEIGHBORS_PER_TAG,
        )

    def test_narrow_graph_is_unchanged_by_a_generous_bound(self):
        db = _Db(_wide_memory_rows(3), 3)
        matrix = DirectedCooccurrence(db, max_neighbors_per_tag=64)

        matrix.rebuild()

        # Three co-occurring tags: each points at the other two.
        self.assertEqual(matrix.node_count, 3)
        self.assertEqual(matrix.edge_count, 6)


if __name__ == "__main__":
    unittest.main()
