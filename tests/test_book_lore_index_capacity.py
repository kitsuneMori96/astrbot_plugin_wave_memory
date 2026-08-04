"""BookLore resident sizing: fit the corpus, grow in small steps, never refuse.

BookLore ingests roughly one or two chapters per day, so a hard count ceiling
would eventually break normal ingestion.  Memory is instead controlled by fitting
the startup allocation to the existing corpus and growing incrementally.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("hnswlib")

from engine.book_lore_index import BookLoreIndex


def _vectors(count: int, dimension: int = 8) -> np.ndarray:
    rng = np.random.default_rng(1234)
    return rng.random((count, dimension), dtype=np.float32)


def _index(tmp_path, **kwargs) -> BookLoreIndex:
    return BookLoreIndex(dimension=8, data_dir=str(tmp_path), **kwargs)


def _write_id_maps(tmp_path, *, entities=0, communities=0, notes=0) -> None:
    (tmp_path / "book_lore_id_maps.json").write_text(
        json.dumps(
            {
                "entity_map": {str(i): f"e{i}" for i in range(entities)},
                "community_map": {str(i): f"c{i}" for i in range(communities)},
                "notes_map": {str(i): f"n{i}" for i in range(notes)},
            }
        ),
        encoding="utf-8",
    )


def test_daily_growth_past_initial_allocation_is_never_refused(tmp_path):
    index = _index(tmp_path, max_elements=4)

    # Five entities against an initial allocation of four: must grow, not raise.
    index.add_entities_batch([f"e{i}" for i in range(5)], _vectors(5))

    assert index.entity_count == 5
    assert index.max_elements >= 5


def test_repeated_small_batches_keep_growing(tmp_path):
    index = _index(tmp_path, max_elements=2)

    for day in range(20):
        index.add_entities_batch([f"day{day}-a", f"day{day}-b"], _vectors(2))

    assert index.entity_count == 40
    assert index.max_elements >= 40


def test_single_add_grows_at_the_boundary(tmp_path):
    index = _index(tmp_path, max_elements=2)
    index.add_entities_batch(["a", "b"], _vectors(2))

    index.add_entity("c", _vectors(1)[0])

    assert index.entity_count == 3
    assert index.max_elements >= 3


def test_community_and_notes_also_grow_independently(tmp_path):
    index = _index(
        tmp_path,
        max_elements=100,
        community_max_elements=2,
        notes_max_elements=1,
    )

    index.add_communities_batch(["c1", "c2", "c3"], _vectors(3))
    index.add_notes_batch(["n1", "n2"], _vectors(2))

    assert index.community_count == 3
    assert index.notes_count == 2
    assert index.community_max_elements >= 3
    assert index.notes_max_elements >= 2


def test_startup_fits_existing_corpus_instead_of_a_large_preallocation(tmp_path):
    _write_id_maps(tmp_path, entities=4000, communities=200, notes=40)

    index = _index(
        tmp_path,
        max_elements=50000,
        community_max_elements=10000,
        notes_max_elements=5000,
    )

    # Fitted to corpus + one growth step, well below the initial sizes.
    assert index.max_elements == 4600
    assert index.community_max_elements == 328
    assert index.notes_max_elements == 168


def test_empty_corpus_uses_the_initial_allocation(tmp_path):
    index = _index(tmp_path, max_elements=1234, community_max_elements=56)

    assert index.max_elements == 1234
    assert index.community_max_elements == 56


def test_corpus_larger_than_initial_is_not_truncated(tmp_path):
    _write_id_maps(tmp_path, entities=5000)

    index = _index(tmp_path, max_elements=256)

    assert index.max_elements >= 5000


def test_fit_can_be_disabled_for_explicit_control(tmp_path):
    _write_id_maps(tmp_path, entities=1)

    index = _index(tmp_path, max_elements=9000, fit_to_existing=False)

    assert index.max_elements == 9000


def test_corrupt_id_maps_fall_back_to_initial_allocation(tmp_path):
    (tmp_path / "book_lore_id_maps.json").write_text("{not json", encoding="utf-8")

    index = _index(tmp_path, max_elements=4321)

    assert index.max_elements == 4321


def test_writes_and_search_still_work(tmp_path):
    index = _index(tmp_path, max_elements=8)
    vectors = _vectors(3)
    index.add_entities_batch(["a", "b", "c"], vectors)

    results = index.search_entities(vectors[0], k=2)

    assert index.entity_count == 3
    assert results
    assert results[0][0] == "a"


def test_malformed_initial_allocation_falls_back_to_default(tmp_path):
    index = _index(tmp_path, max_elements=0, community_max_elements=-3)

    assert index.max_elements == 4096
    assert index.community_max_elements == 1024


def test_empty_batches_are_noops(tmp_path):
    index = _index(tmp_path, max_elements=1)

    index.add_entities_batch([], _vectors(0))
    index.add_communities_batch([], _vectors(0))
    index.add_notes_batch([], _vectors(0))

    assert index.entity_count == 0
    assert index.community_count == 0
    assert index.notes_count == 0
